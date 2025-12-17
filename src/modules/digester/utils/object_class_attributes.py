# Copyright (c) 2025 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple, cast
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
from ..prompts.objectClassAttributesPrompts import (
    get_filter_duplicates_system_prompt,
    get_filter_duplicates_user_prompt,
    get_object_class_schema_system_prompt,
    get_object_class_schema_user_prompt,
)
from ..schema import ObjectClassSchemaResponse
from .metadata_helper import extract_summary_and_tags
from .parallel_docs import process_grouped_chunks_in_parallel

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
    chunk_index: int,
    chunk_text: str,
    object_class: str,
    job_id: UUID,
    total_chunks: Optional[int] = None,
    doc_id: Optional[UUID] = None,
    doc_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Run the attribute-extraction LLM on a single chunk and normalize the result to:
        { attribute_name: attribute_info_dict }
    """
    try:
        logger.info("[Digester:Attributes] LLM call for chunk %s", chunk_index + 1)

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
                logger.warning("[Digester:Attributes] Empty or unsupported result for chunk %s", chunk_index + 1)
                return {}

        return {name: info.model_dump() for name, info in parsed.attributes.items()}

    except Exception as exc:
        logger.error("[Digester:Attributes] Chunk %d failed: %s", chunk_index + 1, exc)
        total = total_chunks or 0
        msg = f"[Digester:Attributes] Chunk {chunk_index + 1}/{total if total else '?'} call failed: {exc}"
        if doc_id:
            msg = f"{msg} (Doc: {doc_id})"
        append_job_error(job_id, msg)
        return {}
    finally:
        update_job_progress(job_id, stage=JobStage.processing_chunks)


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
        stage="processing_chunks",
        current_doc_processed_chunks=0,
        current_doc_total_chunks=total_chunks,
        message="Processing chunks for document",
    )

    chain = _build_attribute_chain(total_chunks)

    tasks = [
        _extract_from_single_chunk(
            chain,
            chunk_index=i,
            chunk_text=chunk_text,
            object_class=object_class,
            job_id=job_id,
            total_chunks=total_chunks,
            doc_id=doc_id,
            doc_metadata=doc_metadata,
        )
        for i, chunk_text in enumerate(doc_chunks)
    ]
    results = await asyncio.gather(*tasks)

    logger.info("[Digester:Attributes] Extraction completed for %s chunks in doc %s", total_chunks, doc_id)
    return results


async def _merge_attribute_candidates(
    *,
    object_class: str,
    per_chunk: List[Dict[str, Dict[str, Any]]],
    job_id: UUID,
) -> Dict[str, Dict[str, Any]]:
    """
    Take a list of partial attribute dicts (one per chunk), group them by name,
    and resolve duplicates via LLM (with a safe fallback).
    """
    # TODO
    # Check if candidates are in correct form
    candidates: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for idx, partial in enumerate(per_chunk):
        if not partial:
            continue

        for attr_name, attr_info in partial.items():
            info_copy = dict(attr_info)
            info_copy.setdefault("name", attr_name)
            candidates[attr_name].append(
                {
                    "info": info_copy,
                }
            )

    if not candidates:
        return {}

    if not any(len(attr_list) > 1 for attr_list in candidates.values()):
        return {name: infos[0]["info"] for name, infos in candidates.items()}

    # slow path: need LLM to decide
    update_job_progress(
        job_id,
        stage=JobStage.resolving_duplicates,
        message=f"Resolving duplicate attributes for {object_class}",
    )

    dedupe_chain = _build_dedupe_chain()
    payload = json.dumps(candidates, ensure_ascii=False)

    # TODO
    # Check if payload is in correct form
    try:
        logger.info("[Digester:Attributes] Resolving duplicates for %s", object_class)
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
    chunk_details: List[Tuple[int, str]] | None = None,
    doc_metadata_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Main entrypoint: extract attributes from pre-selected chunks (no re-chunking).

    Args:
        chunks: list of chunk texts (already selected as relevant)
        object_class: name of object class to extract attributes for
        job_id: job id for progress reporting
        chunk_details: parallel list of (original_chunk_index, doc_uuid) pairs for traceability

    Returns:
        JSON-serializable dict:
            {
                "result": {"attributes": {...}},
                "relevantChunks": [ {"docUuid": ..., "chunkIndex": ...}, ... ]
            }
    """
    if chunk_details is None:
        chunk_details = [(i, "") for i in range(len(chunks))]

    logger.info("[Digester:Attributes] Processing %d pre-selected chunks", len(chunks))

    doc_to_chunks: Dict[str, List[Tuple[int, int, str]]] = {}
    for idx, (original_idx, doc_uuid) in enumerate(chunk_details):
        doc_to_chunks.setdefault(doc_uuid, []).append((idx, original_idx, chunks[idx]))

    total_documents = len(doc_to_chunks)
    logger.info(
        "[Digester:Attributes] Processing chunks from %d documents: %s",
        total_documents,
        {doc_uuid: len(doc_chunks) for doc_uuid, doc_chunks in doc_to_chunks.items()},
    )

    update_job_progress(
        job_id,
        total_documents=total_documents,
        processed_documents=0,
        message="Processing selected chunks",
    )

    all_per_chunk: List[Dict[str, Dict[str, Any]]] = []
    relevant_chunk_info: List[Dict[str, Any]] = []

    async def _extract_for_doc(
        doc_uuid: UUID, doc_chunks: List[Tuple[int, int, str]], doc_index: int
    ) -> Tuple[List[Dict[str, Dict[str, Any]]], List[Dict[str, Any]]]:
        """Extract attributes from chunks of a single document."""
        num_chunks = len(doc_chunks)
        update_job_progress(
            job_id,
            stage=JobStage.processing_chunks,
            message=(f"Processing {num_chunks} chunks from document {doc_index}/{total_documents} for {object_class}"),
        )

        # Get metadata for this document
        doc_metadata = None
        if doc_metadata_map:
            doc_metadata = doc_metadata_map.get(str(doc_uuid))

        # keep original order inside this document
        doc_chunks_text = [chunk_text for _, _, chunk_text in doc_chunks]
        per_chunk_for_doc = await _extract_attributes_for_doc(
            object_class=object_class,
            doc_chunks=doc_chunks_text,
            job_id=job_id,
            doc_id=doc_uuid,
            doc_metadata=doc_metadata,
        )

        # stitch back to global order and record relevant ones
        doc_per_chunk = []
        doc_relevant_chunks = []
        for in_doc_idx, partial in enumerate(per_chunk_for_doc):
            array_idx, original_idx, _ = doc_chunks[in_doc_idx]
            doc_per_chunk.append(partial)
            if partial:
                doc_relevant_chunks.append({"docUuid": doc_uuid, "chunkIndex": original_idx})

        return doc_per_chunk, doc_relevant_chunks

    # Process all documents in parallel using the generic function
    results = await process_grouped_chunks_in_parallel(
        doc_to_chunks=doc_to_chunks,
        job_id=job_id,
        extractor=_extract_for_doc,
        logger_scope="Digester:Attributes",
        total_documents=total_documents,
    )

    # Collect results from all documents
    for doc_per_chunk, doc_relevant_chunks in results:
        all_per_chunk.extend(doc_per_chunk)
        relevant_chunk_info.extend(doc_relevant_chunks)

    # dedupe & merge
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

    logger.info("[Digester:Attributes] Extraction complete. Unique attributes: %d", len(merged_attributes))
    update_job_progress(job_id, stage=JobStage.schema_ready, message="Attribute extraction complete")

    return {"result": {"attributes": merged_attributes}, "relevantChunks": relevant_chunk_info}
