# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

from src.common.enums import JobStage
from src.common.jobs import (
    append_job_error,
    update_job_progress,
)
from src.common.langfuse import langfuse_handler
from src.common.llm import get_default_llm, make_basic_chain
from src.common.utils.normalize import normalize_chunk_pair
from src.modules.digester.prompts.rest.attributes_prompts import (
    get_fill_missing_details_system_prompt,
    get_fill_missing_details_user_prompt,
    get_filter_duplicates_system_prompt,
    get_filter_duplicates_user_prompt,
    get_object_class_schema_system_prompt,
    get_object_class_schema_user_prompt,
)
from src.modules.digester.schema import AttributeResponse
from src.modules.digester.utils.attribute_filters import (
    filter_ignored_attributes,
    ignore_attribute_name,
    normalize_readability_flags,
)
from src.modules.digester.utils.concurrent_chunk_runner import run_chunk_groups_concurrently
from src.modules.digester.utils.merges import merge_attribute_candidates
from src.modules.digester.utils.metadata_helper import extract_summary_and_tags

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
            ("system", get_fill_missing_details_system_prompt + "\n\n{format_instructions}"),
            ("user", get_fill_missing_details_user_prompt),
        ]
    ).partial(format_instructions=parser.get_format_instructions())
    return make_basic_chain(prompt, llm, parser)


def _attach_relevant_documentations_per_attribute(
    attributes: Dict[str, Dict[str, Any]],
    attribute_chunk_pairs: Dict[str, Set[Tuple[str, str]]],
) -> Dict[str, Dict[str, Any]]:
    """Attach per-attribute relevantDocumentations in camelCase."""
    enriched: Dict[str, Dict[str, Any]] = {}
    normalized_pairs: Dict[str, Set[Tuple[str, str]]] = {}

    for raw_name, pairs in attribute_chunk_pairs.items():
        normalized = str(raw_name).strip().lower()
        if not normalized:
            continue
        if normalized not in normalized_pairs:
            normalized_pairs[normalized] = set()
        normalized_pairs[normalized].update(pairs)

    for attr_name, attr_info in attributes.items():
        info = dict(attr_info)
        direct_pairs = attribute_chunk_pairs.get(attr_name, set())
        if direct_pairs:
            sorted_pairs = sorted(direct_pairs, key=lambda pair: (pair[0], pair[1]))
        else:
            fallback_pairs = normalized_pairs.get(str(attr_name).strip().lower(), set())
            sorted_pairs = sorted(fallback_pairs, key=lambda pair: (pair[0], pair[1]))
        info["relevantDocumentations"] = [{"docId": doc_id, "chunkId": chunk_id} for doc_id, chunk_id in sorted_pairs]
        enriched[attr_name] = info

    return enriched


async def _extract_from_single_chunk(
    chain: Any,
    *,
    chunk_text: str,
    object_class: str,
    job_id: UUID,
    chunk_id: Optional[UUID] = None,
    chunk_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Run the attribute-extraction LLM on a single chunk and normalize the result to:
        { attribute_name: attribute_info_dict }
    """
    try:
        logger.info("[Digester:Attributes] LLM call for chunk %s", chunk_id)

        # Extract summary and tags from chunk metadata
        summary, tags = extract_summary_and_tags(chunk_metadata)

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

        return {
            name: info.model_dump(exclude={"relevant_documentations", "scimAttribute"})
            for name, info in parsed.attributes.items()
        }

    except Exception as exc:
        error_message = f"[Digester:Attributes] Failed to process chunk {chunk_id}: {exc}"
        logger.exception(error_message)
        append_job_error(job_id, error_message)
        return {}


async def _extract_attributes_from_chunks(
    *,
    object_class: str,
    chunks_for_chunk_id: List[str],
    job_id: UUID,
    chunk_id: UUID,
    chunk_metadata: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Dict[str, Any]]]:
    """
    Extract attribute maps from all chunks associated with one chunk_id.
    Returns a list aligned with chunks_for_chunk_id: index -> {attribute_name: info}
    """
    total_chunks = len(chunks_for_chunk_id)
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
            chunk_id=chunk_id,
            chunk_metadata=chunk_metadata,
        )
        for i, chunk_text in enumerate(chunks_for_chunk_id)
    ]
    results = list(await asyncio.gather(*tasks))

    logger.info("[Digester:Attributes] Extraction completed for chunk %s", chunk_id)
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

        return {
            name: info.model_dump(exclude={"relevant_documentations", "scimAttribute"})
            for name, info in parsed.attributes.items()
        }

    except Exception as exc:
        logger.error("[Digester:Attributes] Fill from chunk failed: %s", exc)
        return {}


async def fill_missing_details(
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
    chunk_metadata_map: Optional[Dict[str, Dict[str, Any]]] = None,
    chunk_id_to_doc_id: Optional[Dict[str, str]] = None,
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
        chunk_details: Optional list of chunk IDs for each chunk (default: None)
        chunk_metadata_map: Optional metadata mapping for chunk IDs (default: None)
        chunk_id_to_doc_id: Optional mapping of chunk ID to doc ID

    Returns:
        Dict containing:
        - "result": Dict with "attributes" key containing extracted attribute information
        - "relevantDocumentations": List of chunks that contained relevant attribute information
    """
    if chunk_details is None:
        chunk_details = [""] * len(chunks)

    logger.info(
        "[Digester:Attributes] Processing %d pre-selected chunks for %s (chunk IDs: %s)",
        len(chunks),
        object_class,
        chunk_details,
    )
    chunks_by_id: Dict[str, List[str]] = {}
    for chunk_text, chunk_id in zip(chunks, chunk_details, strict=False):
        chunks_by_id.setdefault(chunk_id, []).append(chunk_text)

    total_chunk_ids = len(chunks_by_id)

    await update_job_progress(
        job_id,
        total_processing=total_chunk_ids,
        processing_completed=0,
        message="Processing chunks and try to extract relevant information",
    )

    all_per_chunk: List[Dict[str, Dict[str, Any]]] = []
    relevant_chunks: List[Dict[str, Any]] = []
    attribute_chunk_pairs: Dict[str, Set[Tuple[str, str]]] = {}

    async def _extract_for_chunk_id(chunk_id: UUID, chunks_for_chunk_id: List[str]):
        chunk_metadata = chunk_metadata_map.get(str(chunk_id)) if chunk_metadata_map else None

        per_chunk_results = await _extract_attributes_from_chunks(
            object_class=object_class,
            chunks_for_chunk_id=chunks_for_chunk_id,
            job_id=job_id,
            chunk_id=chunk_id,
            chunk_metadata=chunk_metadata,
        )

        if any(bool(x) for x in per_chunk_results):
            chunk_id_str = str(chunk_id)
            doc_id = chunk_id_to_doc_id.get(chunk_id_str) if chunk_id_to_doc_id else None
            if doc_id:
                return per_chunk_results, [{"doc_id": doc_id, "chunk_id": chunk_id_str}]
            logger.warning(
                "[Digester:Attributes] Missing docId for chunk %s, skipping relevant chunk mapping",
                chunk_id_str,
            )
        return per_chunk_results, []

    results = await run_chunk_groups_concurrently(
        chunks_by_id=chunks_by_id,
        job_id=job_id,
        extractor=_extract_for_chunk_id,
        logger_scope="Digester:Attributes",
        total_groups=total_chunk_ids,
    )

    for chunk_per_group, chunk_relevant in results:
        all_per_chunk.extend(chunk_per_group)
        relevant_chunks.extend(chunk_relevant)

        normalized_pairs = [normalize_chunk_pair(chunk_ref) for chunk_ref in chunk_relevant]
        valid_pairs = [pair for pair in normalized_pairs if pair is not None]
        if not valid_pairs:
            continue

        extracted_attribute_names = {
            str(attr_name).strip()
            for partial in chunk_per_group
            if isinstance(partial, dict)
            for attr_name in partial.keys()
            if isinstance(attr_name, str) and attr_name.strip()
        }
        for attr_name in extracted_attribute_names:
            seen_pairs = attribute_chunk_pairs.setdefault(attr_name, set())
            for doc_id, chunk_id in valid_pairs:
                seen_pairs.add((doc_id, chunk_id))

    merged_attributes = await merge_attribute_candidates(
        object_class=object_class,
        per_chunk=all_per_chunk,
        job_id=job_id,
        build_dedupe_chain=_build_dedupe_chain,
    )

    filtered_attributes = filter_ignored_attributes(merged_attributes)
    removed_attributes = [name for name in merged_attributes if ignore_attribute_name(name)]
    if removed_attributes:
        logger.info(
            "[Digester:Attributes] Removed %d ignored attributes during postprocessing: %s",
            len(removed_attributes),
            sorted(removed_attributes),
        )

    filled_attributes = await fill_missing_details(
        object_class=object_class,
        attributes=filtered_attributes,
        chunks=chunks,
        job_id=job_id,
    )
    postprocessed_attributes = normalize_readability_flags(filled_attributes)
    attributes_with_references = _attach_relevant_documentations_per_attribute(
        postprocessed_attributes,
        attribute_chunk_pairs,
    )

    await update_job_progress(job_id, stage=JobStage.schema_ready, message="Attribute extraction complete")

    return {"result": {"attributes": attributes_with_references}, "relevantDocumentations": relevant_chunks}
