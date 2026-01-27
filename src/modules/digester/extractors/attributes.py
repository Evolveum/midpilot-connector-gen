# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional, cast
from uuid import UUID

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables.config import RunnableConfig

from ....common.enums import JobStage
from ....common.jobs import (
    append_job_error,
    update_job_progress,
)
from ....common.langfuse import langfuse_handler
from ....common.llm import get_default_llm, make_basic_chain
from ..prompts.attributes_prompts import (
    get_filter_duplicates_system_prompt,
    get_filter_duplicates_user_prompt,
    get_object_class_schema_system_prompt,
    get_object_class_schema_user_prompt,
)
from ..schema import ObjectClassSchemaResponse
from ..utils.metadata_helper import extract_summary_and_tags
from ..utils.parallel_docs import process_grouped_chunks_in_parallel

logger = logging.getLogger(__name__)


def _build_attribute_chain(total_chunks: int) -> Any:
    """
    Build the LLM chain for extracting attributes from a single chunk.
    """
    parser: PydanticOutputParser[ObjectClassSchemaResponse] = PydanticOutputParser(
        pydantic_object=ObjectClassSchemaResponse
    )
    llm = get_default_llm()
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", get_object_class_schema_system_prompt + "\n\n{format_instructions}"),
            ("user", get_object_class_schema_user_prompt),
        ]
    ).partial(total=total_chunks, format_instructions=parser.get_format_instructions())
    return make_basic_chain(prompt, llm, parser)


def _build_dedupe_chain() -> Any:
    """
    Build the LLM chain used to resolve attribute duplicates across chunks.
    """
    parser: PydanticOutputParser[ObjectClassSchemaResponse] = PydanticOutputParser(
        pydantic_object=ObjectClassSchemaResponse
    )
    llm = get_default_llm()
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", get_filter_duplicates_system_prompt + "\n\n{format_instructions}"),
            ("user", get_filter_duplicates_user_prompt),
        ]
    ).partial(format_instructions=parser.get_format_instructions())
    return make_basic_chain(prompt, llm, parser)


async def _extract_from_single_chunk(
    chain: Any,
    *,
    chunk_text: str,
    object_class: str,
    job_id: UUID,
    doc_id: Optional[UUID] = None,
    doc_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Run the attribute-extraction LLM on a single chunk and normalize the result to:
        { attribute_name: attribute_info_dict }
    """
    try:
        logger.info("[Digester:Attributes] LLM call for document %s", doc_id)

        # Extract summary and tags from doc metadata
        summary, tags = extract_summary_and_tags(doc_metadata)

        result = await chain.ainvoke(
            {
                "chunk": chunk_text,
                "object_class": object_class,
                "summary": summary,
                "tags": tags,
            },
            config={"callbacks": [langfuse_handler]},
        )

        if isinstance(result, ObjectClassSchemaResponse):
            parsed = result
        elif isinstance(result, dict):
            parsed = ObjectClassSchemaResponse.model_validate(result)
        else:
            content = getattr(result, "content", None)
            if isinstance(content, str) and content.strip():
                parsed = ObjectClassSchemaResponse.model_validate(json.loads(content))
            else:
                return {}

        return {name: info.model_dump() for name, info in parsed.attributes.items()}

    except Exception as exc:
        logger.error("[Digester:Attributes] Document %s call failed: %s", doc_id, exc)
        msg = f"[Digester:Attributes] Document {doc_id} call failed: {exc}"
        append_job_error(job_id, msg)
        return {}


async def _extract_attributes_for_doc(
    *,
    object_class: str,
    doc_chunks: List[str],
    job_id: UUID,
    doc_id: UUID,
    doc_metadata: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Dict[str, Any]]]:
    """
    Extract attribute maps for all chunks belonging to a single document.
    Returns a list aligned with doc_chunks: index -> {attribute_name: info}
    """
    total_chunks = len(doc_chunks)
    update_job_progress(
        job_id,
        stage=JobStage.processing_chunks,
        message="Processing chunks and try to extract relevant information",
    )

    chain = _build_attribute_chain(total_chunks)

    tasks = [
        _extract_from_single_chunk(
            chain,
            chunk_text=chunk_text,
            object_class=object_class,
            job_id=job_id,
            doc_id=doc_id,
            doc_metadata=doc_metadata,
        )
        for i, chunk_text in enumerate(doc_chunks)
    ]
    results = list(await asyncio.gather(*tasks))

    logger.info("[Digester:Attributes] Extraction completed for document %s", doc_id)
    return results


async def _merge_attribute_candidates(
    *,
    object_class: str,
    per_chunk: List[Dict[str, Dict[str, Any]]],
    job_id: UUID,
) -> Dict[str, Dict[str, Any]]:
    candidates: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    """
        TODO
    """

    for partial in per_chunk:
        if not partial:
            continue
        for attr_name, attr_info in partial.items():
            info_copy = dict(attr_info)
            info_copy.setdefault("name", attr_name)
            candidates[attr_name].append({"info": info_copy})

    if not candidates:
        return {}

    if not any(len(v) > 1 for v in candidates.values()):
        return {name: infos[0]["info"] for name, infos in candidates.items()}

    update_job_progress(
        job_id,
        stage=JobStage.resolving_duplicates,
        message=f"Resolving duplicate attributes for {object_class}",
    )

    dedupe_chain = _build_dedupe_chain()
    payload = json.dumps(candidates, ensure_ascii=False)

    try:
        result = await dedupe_chain.ainvoke(
            {
                "object_class": object_class,
                "candidates_json": payload,
                "guaranteed_candidates_per_name": True,
            },
            config=RunnableConfig(callbacks=[langfuse_handler]),
        )

        if isinstance(result, ObjectClassSchemaResponse):
            parsed = result
        else:
            content = getattr(result, "content", None)
            parsed = (
                ObjectClassSchemaResponse.model_validate(json.loads(content))
                if content
                else ObjectClassSchemaResponse()
            )

        return {name: info.model_dump() for name, info in parsed.attributes.items()}

    except Exception as exc:
        logger.error("[Digester:Attributes] Dedupe failed: %s", exc)
        fallback: Dict[str, Dict[str, Any]] = {}
        object_class_lower = object_class.lower()
        for attr_name, attr_list in candidates.items():
            best = max(
                attr_list,
                key=lambda c: int(object_class_lower in (c["info"].get("description", "").lower())),
            )
            fallback[attr_name] = cast(Dict[str, Any], best["info"])
        return fallback


async def extract_attributes(
    chunks: List[str],
    object_class: str,
    job_id: UUID,
    chunk_details: List[str] | None = None,
    doc_metadata_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Extract object class attributes from document chunks using LLM analysis.

    Processes chunks of text to identify and extract attribute information including
    names, types, descriptions, and metadata for a specific object class. Uses parallel
    processing for efficiency and includes duplicate resolution across chunks.

    Args:
        chunks: List of text chunks to analyze for attribute information
        object_class: Target object class for attribute extraction context
        job_id: UUID for job tracking and progress updates
        chunk_details: Optional list of document UUIDs for each chunk (default: None)
        doc_metadata_map: Optional metadata mapping for documents (default: None)

    Returns:
        Dict containing:
        - "result": Dict with "attributes" key containing extracted attribute information
        - "relevantChunks": List of chunks that contained relevant attribute information
    """
    if chunk_details is None:
        chunk_details = [""] * len(chunks)

    logger.info("[Digester:Attributes] Processing %d pre-selected chunks", len(chunks))

    doc_to_chunks: Dict[str, List[str]] = {}
    for chunk_text, doc_uuid in zip(chunks, chunk_details, strict=False):
        doc_to_chunks.setdefault(doc_uuid, []).append(chunk_text)

    total_documents = len(doc_to_chunks)

    update_job_progress(
        job_id,
        total_processing=total_documents,
        processing_completed=0,
        message="Processing chunks and try to extract relevant information",
    )

    all_per_chunk: List[Dict[str, Dict[str, Any]]] = []
    relevant_docs: List[Dict[str, Any]] = []

    async def _extract_for_doc(doc_uuid: UUID, doc_chunks: List[str]):
        doc_metadata = doc_metadata_map.get(str(doc_uuid)) if doc_metadata_map else None

        per_chunk_for_doc = await _extract_attributes_for_doc(
            object_class=object_class,
            doc_chunks=doc_chunks,
            job_id=job_id,
            doc_id=doc_uuid,
            doc_metadata=doc_metadata,
        )

        if any(bool(x) for x in per_chunk_for_doc):
            return per_chunk_for_doc, [{"docUuid": str(doc_uuid)}]
        return per_chunk_for_doc, []

    results = await process_grouped_chunks_in_parallel(
        doc_to_chunks=doc_to_chunks,
        job_id=job_id,
        extractor=_extract_for_doc,
        logger_scope="Digester:Attributes",
        total_documents=total_documents,
    )

    for doc_per_chunk, doc_relevant in results:
        all_per_chunk.extend(doc_per_chunk)
        relevant_docs.extend(doc_relevant)

    update_job_progress(
        job_id,
        stage="merging",
        message=f"Merging and deduplicating attributes for {object_class}",
    )

    merged_attributes = await _merge_attribute_candidates(
        object_class=object_class,
        per_chunk=all_per_chunk,
        job_id=job_id,
    )

    update_job_progress(job_id, stage=JobStage.schema_ready, message="Attribute extraction complete")

    return {"result": {"attributes": merged_attributes}, "relevantChunks": relevant_docs}
