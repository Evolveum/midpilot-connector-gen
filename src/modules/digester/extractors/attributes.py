# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

from ....common.enums import JobStage
from ....common.jobs import (
    append_job_error,
    update_job_progress,
)
from ....common.langfuse import langfuse_handler
from ....common.llm import get_default_llm, make_basic_chain
from ..prompts.attributes_prompts import (
    get_fill_missing_attributes_system_prompt,
    get_fill_missing_attributes_user_prompt,
    get_filter_duplicates_system_prompt,
    get_filter_duplicates_user_prompt,
    get_object_class_schema_system_prompt,
    get_object_class_schema_user_prompt,
)
from ..schema import AttributeResponse
from ..utils.merges import merge_attribute_candidates
from ..utils.metadata_helper import extract_summary_and_tags
from ..utils.parallel_docs import process_grouped_chunks_in_parallel

logger = logging.getLogger(__name__)


def _build_attribute_chain(total_chunks: int) -> Any:
    """
    Build the LLM chain for extracting attributes from a single chunk.
    """
    parser: PydanticOutputParser[AttributeResponse] = PydanticOutputParser(pydantic_object=AttributeResponse)
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
    parser: PydanticOutputParser[AttributeResponse] = PydanticOutputParser(pydantic_object=AttributeResponse)
    llm = get_default_llm()
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", get_filter_duplicates_system_prompt + "\n\n{format_instructions}"),
            ("user", get_filter_duplicates_user_prompt),
        ]
    ).partial(format_instructions=parser.get_format_instructions())
    return make_basic_chain(prompt, llm, parser)


def _build_fill_missing_chain() -> Any:
    """
    Build the LLM chain used to fill missing attribute information from documentation.
    """
    parser: PydanticOutputParser[AttributeResponse] = PydanticOutputParser(pydantic_object=AttributeResponse)
    llm = get_default_llm()
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", get_fill_missing_attributes_system_prompt + "\n\n{format_instructions}"),
            ("user", get_fill_missing_attributes_user_prompt),
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

        if isinstance(result, AttributeResponse):
            parsed = result
        elif isinstance(result, dict):
            parsed = AttributeResponse.model_validate(result)
        else:
            content = getattr(result, "content", None)
            if isinstance(content, str) and content.strip():
                parsed = AttributeResponse.model_validate(json.loads(content))
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
    await update_job_progress(
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


async def _fill_from_single_chunk(
    chain: Any,
    *,
    chunk_text: str,
    object_class: str,
    attributes_json: str,
    job_id: UUID,
) -> Dict[str, Dict[str, Any]]:
    """
    Run the fill-missing LLM on a single chunk to fill null attribute values.
    Returns updated attributes for this chunk.
    """
    try:
        result = await chain.ainvoke(
            {
                "object_class": object_class,
                "attributes_json": attributes_json,
                "docs_payload": chunk_text,
            },
            config={"callbacks": [langfuse_handler]},
        )

        if isinstance(result, AttributeResponse):
            parsed = result
        elif isinstance(result, dict):
            parsed = AttributeResponse.model_validate(result)
        else:
            content = getattr(result, "content", None)
            if isinstance(content, str) and content.strip():
                parsed = AttributeResponse.model_validate(json.loads(content))
            else:
                return {}

        return {name: info.model_dump() for name, info in parsed.attributes.items()}

    except Exception as exc:
        logger.error("[Digester:Attributes] Fill from chunk failed: %s", exc)
        return {}


async def fill_missing_attribute_info(
    *,
    object_class: str,
    attributes: Dict[str, Dict[str, Any]],
    chunks: List[str],
    job_id: UUID,
) -> Dict[str, Dict[str, Any]]:
    """
    Fill missing (null) attribute information by re-analyzing documentation chunks.

    Takes deduplicated attributes and runs them through the LLM again with each
    chunk individually to fill in any null/missing parameter values.

    Args:
        object_class: Name of the object class
        attributes: Deduplicated attributes map with potentially null values
        chunks: List of documentation chunks to analyze
        job_id: Job ID for progress tracking

    Returns:
        Updated attributes dictionary with filled information
    """
    if not attributes:
        return attributes

    # Check if there are any null values that need filling
    has_nulls = any(value is None for attr_info in attributes.values() for value in attr_info.values())

    if not has_nulls:
        logger.info("[Digester:Attributes] No null values found, skipping fill step")
        return attributes

    await update_job_progress(
        job_id,
        stage=JobStage.processing_chunks,
        message=f"Filling missing attribute information for {object_class}",
    )

    # Prepare attributes JSON once
    attributes_json = json.dumps(attributes, ensure_ascii=False, indent=2)

    fill_chain = _build_fill_missing_chain()

    logger.info("[Digester:Attributes] Processing %d chunks to fill missing info for %s", len(chunks), object_class)

    # Process each chunk in parallel
    tasks = [
        _fill_from_single_chunk(
            fill_chain,
            chunk_text=chunk_text,
            object_class=object_class,
            attributes_json=attributes_json,
            job_id=job_id,
        )
        for chunk_text in chunks
    ]

    chunk_results = list(await asyncio.gather(*tasks))

    filled_attributes = dict(attributes)

    for chunk_result in chunk_results:
        if not chunk_result:
            continue

        for attr_name, attr_info in chunk_result.items():
            if attr_name not in filled_attributes:
                continue

            # For each field in the attribute, fill if currently null and chunk has value
            for field_name, field_value in attr_info.items():
                if field_value is not None and filled_attributes[attr_name].get(field_name) is None:
                    filled_attributes[attr_name][field_name] = field_value

    logger.info("[Digester:Attributes] Successfully filled missing info for %s", object_class)
    return filled_attributes


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

    logger.info(
        "[Digester:Attributes] Processing %d pre-selected chunks for %s (docs UUIDs: %s)",
        len(chunks),
        object_class,
        chunk_details,
    )
    doc_to_chunks: Dict[str, List[str]] = {}
    for chunk_text, doc_uuid in zip(chunks, chunk_details, strict=False):
        doc_to_chunks.setdefault(doc_uuid, []).append(chunk_text)

    total_documents = len(doc_to_chunks)

    await update_job_progress(
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

    merged_attributes = await merge_attribute_candidates(
        object_class=object_class,
        per_chunk=all_per_chunk,
        job_id=job_id,
        build_dedupe_chain=_build_dedupe_chain,
    )

    # logger.info("[Digester:Attributes] Deduplicated attributes BEFORE fill missing:")
    # logger.info(json.dumps(merged_attributes, indent=2, ensure_ascii=False))

    filled_attributes = await fill_missing_attribute_info(
        object_class=object_class,
        attributes=merged_attributes,
        chunks=chunks,
        job_id=job_id,
    )

    # logger.info("[Digester:Attributes] Filled attributes AFTER fill missing:")
    # logger.info(json.dumps(filled_attributes, indent=2, ensure_ascii=False))

    await update_job_progress(job_id, stage=JobStage.schema_ready, message="Attribute extraction complete")

    return {"result": {"attributes": filled_attributes}, "relevantChunks": relevant_docs}
