# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import copy
import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID

from langchain_core.output_parsers import BaseOutputParser, PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

from modules.digester.utils.chunk_extraction import extract_single_chunk
from src.common.enums import JobStage
from src.common.jobs import (
    append_job_error,
    update_job_progress,
)
from src.common.langfuse import langfuse_handler
from src.common.llm import get_default_llm, make_basic_chain
from src.common.utils.normalize import normalize_chunk_pair
from src.config import config
from src.modules.digester.prompts.rest.attributes_prompts import (
    attribute_deduplication_system_prompt,
    attribute_deduplication_user_prompt,
    get_attribute_discovery_system_prompt,
    get_attribute_discovery_user_prompt,
    get_build_from_sequences_system_prompt,
    get_build_from_sequences_user_prompt,
    get_consolidate_attributes_system_prompt,
    get_consolidate_attributes_user_prompt,
    get_fill_missing_details_system_prompt,
    get_fill_missing_details_user_prompt,
    get_object_class_schema_system_prompt,
    get_object_class_schema_user_prompt,
)
from src.modules.digester.schema import (
    AttributeBuildResponse,
    AttributeDedupResponse,
    AttributeDiscoveryResponse,
    AttributeInfoRest,
    AttributeProcessingInfo,
    AttributeResponse,
    DiscoveryAttribute,
    DocSequenceItem,
)
from src.modules.digester.utils.attribute_filters import (
    filter_ignored_attributes,
    ignore_attribute_name,
    normalize_readability_flags,
)
from src.modules.digester.utils.concurrent_chunk_runner import run_chunks_concurrently
from src.modules.digester.utils.merges import merge_attribute_candidates
from src.modules.digester.utils.metadata_helper import extract_summary_and_tags

logger = logging.getLogger(__name__)


def _format_attributes_as_table(attributes: Dict[str, AttributeInfoRest]) -> str:
    """Format consolidated attributes as a fixed-width ASCII table for logging/display."""
    if not attributes:
        return "No attributes extracted."

    columns: List[Tuple[str, int]] = [
        ("name", 18),
        ("description", 56),
        ("type", 12),
        ("format", 12),
        ("mandatory", 10),
        ("updatable", 10),
        ("creatable", 10),
        ("readable", 10),
        ("multivalue", 11),
        ("returnedByDefault", 18),
    ]

    def _normalize_cell(value: Any, width: int) -> str:
        if value is None:
            text = "-"
        else:
            text = str(value)

        # Keep one-line cells and avoid table-breaking characters.
        text = text.replace("|", "/")
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            text = "-"

        if len(text) > width:
            text = text[: max(width - 3, 1)].rstrip() + "..."
        return text.ljust(width)

    def _bool_or_dash(value: Optional[bool]) -> str:
        return "-" if value is None else str(value).lower()

    header = "| " + " | ".join(name.ljust(width) for name, width in columns) + " |"
    separator = "+-" + "-+-".join("-" * width for _, width in columns) + "-+"

    rows: List[str] = []
    for attr_name, attr_info in sorted(attributes.items()):
        values = [
            attr_name,
            attr_info.description,
            attr_info.type,
            attr_info.format,
            _bool_or_dash(attr_info.mandatory),
            _bool_or_dash(attr_info.updatable),
            _bool_or_dash(attr_info.creatable),
            _bool_or_dash(attr_info.readable),
            _bool_or_dash(attr_info.multivalue),
            _bool_or_dash(attr_info.returnedByDefault),
        ]
        row = "| " + " | ".join(
            _normalize_cell(value, width) for value, (_, width) in zip(values, columns, strict=True)
        ) + " |"
        rows.append(row)

    return "\n".join([separator, header, separator, *rows, separator])


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
    parser: BaseOutputParser = PydanticOutputParser(pydantic_object=AttributeDedupResponse)
    llm = get_default_llm()
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", attribute_deduplication_system_prompt + "\n\n{format_instructions}"),
            ("human", attribute_deduplication_user_prompt),
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

def _build_build_attr_chain() -> Any:
    """
    Build the LLM chain used to build complete attribute information from sequences.
    """
    parser: PydanticOutputParser[AttributeBuildResponse] = PydanticOutputParser(pydantic_object=AttributeBuildResponse)
    llm = get_default_llm()
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", get_build_from_sequences_system_prompt + "\n\n{format_instructions}"),
            ("user", get_build_from_sequences_user_prompt),
        ]
    ).partial(format_instructions=parser.get_format_instructions())
    return make_basic_chain(prompt, llm, parser)

def _build_consolidation_chain() -> Any:
    """
    Build the LLM chain used for final consolidation of attributes.
    """
    parser: PydanticOutputParser[AttributeBuildResponse] = PydanticOutputParser(pydantic_object=AttributeBuildResponse)
    llm = get_default_llm()
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", get_consolidate_attributes_system_prompt + "\n\n{format_instructions}"),
            ("user", get_consolidate_attributes_user_prompt),
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

async def _build_attr_from_sequences(chain: Any, object_class: str, attr: AttributeProcessingInfo, use_steps: bool) -> AttributeProcessingInfo | None:
    """
    Calls llm on existing AttributeProcessingInfo object with sequences, optionally in steps.
    Primary function is to fill missing details in the AttributeInfoRest object based on the sequences provided.
    Secondary is to fix issues in already existing details, type, description, etc. based on the sequences.

    Args:
        chain: LLM chain to use for extraction
        object_class: Name of the object class
        attr: AttributeProcessingInfo object containing existing attribute information and sequences
        use_steps: Whether to use steps when processing sequences or process all at once (if false, the whole sequence list will be used in one call)
    Returns:
        AttributeProcessingInfo object with filled and potentially corrected attribute information
    """

    seq_step = len(attr.relevant_sequences) if not use_steps else config.digester.build_from_sequences_step_size

    for begin in range(0, len(attr.relevant_sequences), seq_step):
        end = min(begin + seq_step, len(attr.relevant_sequences))
        attr_temp = copy.deepcopy(attr)
        attr_temp.relevant_sequences = attr_temp.relevant_sequences[begin:end]
        attr_json = json.dumps(attr_temp.model_dump(exclude={'relevant_documentations'}), ensure_ascii=False, indent=2)
        try:
            result = await chain.ainvoke(
                {
                    "object_class": object_class,
                    "attribute_json": attr_json,
                },
                config={"callbacks": [langfuse_handler]},
            )

            if isinstance(result, AttributeBuildResponse):
                parsed = result
            elif isinstance(result, dict):
                parsed = AttributeBuildResponse.model_validate(result)
            else:
                content = getattr(result, "content", None)
                if isinstance(content, str) and content.strip():
                    parsed = AttributeBuildResponse.model_validate(json.loads(content))
                else:
                    return None
                
            for param in ["type", "format", "description", "mandatory", "updatable", "creatable", "readable", "multivalue", "returnedByDefault"]:
                value = getattr(parsed, param, None)
                if value is not None:
                    setattr(attr, param, value)
        
        except Exception as exc:
            logger.warning("[Digester:Attributes] Build from sequences failed for attribute %s: %s, sequences number: %s - %s", attr.name, exc, begin, end)
            pass

    return attr

    
async def build_attributes_from_sequences(attrs: List[AttributeProcessingInfo], object_class: str) -> List[AttributeProcessingInfo]:
    """
    Run the build-from-sequences chain for each attribute that has relevant sequences, in order to fill missing details and correct existing ones based on the sequences.
    
    Args:
        attrs: List of AttributeProcessingInfo objects to process
        object_class: Name of the object class for context
    """

    build_chain = _build_build_attr_chain()

    tasks = [
        _build_attr_from_sequences(build_chain, object_class, attr, use_steps=True)
        for attr in attrs if attr.relevant_sequences
    ]

    all_builded_attrs = await asyncio.gather(*tasks)
    filtered_builded_attrs = [attr for attr in all_builded_attrs if attr is not None]

    return filtered_builded_attrs

async def consolidate_attributes(attrs: List[AttributeProcessingInfo], object_class: str) -> AttributeResponse:
    """
    Final consolidation of attributes - one final LLM call with all of the sequences for each attribute to correct any issues.
    Transforms AttributeProcessingInfo objects into AttributeInfoRest objects and creates an AttributeResponse object for the final output.
    Args:
        attrs: List of AttributeProcessingInfo objects to consolidate
        object_class: Name of the object class for context
    Returns:
        An AttributeResponse object with consolidated and finalized attribute information ready for output
    """

    build_chain = _build_consolidation_chain()

    tasks = []
    for attr in attrs:
        if attr.relevant_sequences:
            tasks.append(_build_attr_from_sequences(build_chain, object_class, attr, use_steps=False))
        else:
            logger.warning("[Digester:Attributes] Attribute %s has no relevant sequences; skipping final consolidation", attr.name)
            tasks.append(None)

    processed_attrs = await asyncio.gather(*tasks)

    consolidated_attrs: AttributeResponse = AttributeResponse(attributes={})
    for attr in processed_attrs:
        if attr is None:
            continue
        consolidated_attrs.attributes[attr.name] = AttributeInfoRest(
            type=attr.type,
            format=attr.format,
            description=attr.description,
            mandatory=attr.mandatory,
            updatable=attr.updatable,
            creatable=attr.creatable,
            readable=attr.readable,
            multivalue=attr.multivalue,
            returnedByDefault=attr.returnedByDefault,
            relevant_documentations=attr.relevant_documentations,
            relevant_sequences=[DocSequenceItem(**seq.__dict__) for seq in attr.relevant_sequences],
        )

    return consolidated_attrs


async def extract_attributes(
    chunks: List[str],
    object_class: str,
    job_id: UUID,
    chunk_details: List[str],
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
        chunk_details: List of chunk IDs for each chunk, required
        chunk_metadata_map: Optional metadata mapping for chunk IDs (default: None)
        chunk_id_to_doc_id: Optional mapping of chunk ID to doc ID

    Returns:
        Dict containing:
        - "result": Dict with "attributes" key containing extracted attribute information
        - "relevantDocumentations": List of chunks that contained relevant attribute information
    """
    if not chunk_details:
        logger.error("[Digester:Attributes] chunk_details is required but was empty")
        await update_job_progress(job_id, stage=JobStage.failed, message="No chunk details provided, cannot extract attributes")
        return {"result": {"attributes": {}}, "relevantDocumentations": []}

    if len(chunks) != len(chunk_details):
        logger.error(
            "[Digester:Attributes] Length mismatch: %d chunks vs %d chunk_details",
            len(chunks),
            len(chunk_details),
        )
        await update_job_progress(job_id, stage=JobStage.failed, message="Chunk length mismatch, cannot extract attributes")
        return {"result": {"attributes": {}}, "relevantDocumentations": []}

    if len(chunk_details) != len(set(chunk_details)):
        logger.error("[Digester:Attributes] Duplicate chunk IDs found in chunk_details")
        await update_job_progress(job_id, stage=JobStage.failed, message="Duplicate chunk IDs found, cannot extract attributes")
        return {"result": {"attributes": {}}, "relevantDocumentations": []}

    logger.info(
        "[Digester:Attributes] Processing %d pre-selected chunks for %s (chunk IDs: %s)",
        len(chunks),
        object_class,
        chunk_details,
    )
    chunks_by_id: List[Dict[str, str]] = []

    for chunk_text, chunk_id in zip(chunks, chunk_details):
        chunks_by_id.append({"chunkId": chunk_id, "content": chunk_text})

    total_chunk_ids = len(chunks_by_id)

    await update_job_progress(
        job_id,
        total_processing=total_chunk_ids,
        processing_completed=0,
        message="Processing chunks and try to extract relevant information",
    )

    # all_per_chunk: List[Dict[str, Dict[str, Any]]] = []
    # attribute_chunk_pairs: Dict[str, Set[Tuple[str, str]]] = {}

    all_discovery_results: List[DiscoveryAttribute] = []

    async def _extract_for_chunk_id(chunk_text: str, job_id_ext: UUID, chunk_id: UUID) -> Tuple[List[DiscoveryAttribute], bool]:
        chunk_metadata = chunk_metadata_map.get(str(chunk_id)) if chunk_metadata_map else None

        def parse_fn(result: AttributeDiscoveryResponse) -> List[DiscoveryAttribute]:
            return result.attributes or []

        per_chunk_results, relevant_data = await extract_single_chunk(
            schema=chunk_text,
            pydantic_model=AttributeDiscoveryResponse,
            system_prompt=get_attribute_discovery_system_prompt,
            user_prompt=get_attribute_discovery_user_prompt,
            parse_fn=parse_fn,
            logger_prefix="[Digester:Attributes] ",
            job_id=job_id_ext,
            chunk_id=chunk_id,
            chunk_metadata=chunk_metadata,
            enabled_sequence_checking=True,
            enable_marker_blending=True,
            extra_llm_attrs={"object_class": object_class},
            min_start_sequence_length=config.digester.min_start_sequence_len_attributes,
            max_start_sequence_length=config.digester.max_start_sequence_len_attributes,
            min_end_sequence_length=config.digester.min_end_sequence_len_attributes,
            max_end_sequence_length=config.digester.max_end_sequence_len_attributes,
        )

        # if any(bool(x) for x in per_chunk_results):
        #     chunk_id_str = str(chunk_id)
        #     doc_id = chunk_id_to_doc_id.get(chunk_id_str) if chunk_id_to_doc_id else None
        #     if doc_id:
        #         return per_chunk_results, [{"doc_id": doc_id, "chunk_id": chunk_id_str}]
        #     logger.warning(
        #         "[Digester:Attributes] Missing docId for chunk %s, skipping relevant chunk mapping",
        #         chunk_id_str,
        #     )
        return per_chunk_results, relevant_data

    results = await run_chunks_concurrently(
        chunk_items=chunks_by_id,
        job_id=job_id,
        extractor=_extract_for_chunk_id,
        logger_scope="Digester:Attributes",
    )

    #TODO: delete
    for res, relevant_data, chunk_id in results:
        logger.info(
            "[Digester:Attributes] Discovery results for document %s: %d attributes, relevant: %s, whole attributes: %s",
            chunk_id,
            len(res),
            relevant_data,
            res
        )

    for chunk_results, relevant_data, chunk_id in results:
        logger.info(
            "[Digester:Attributes] Discovery results for document %s: %d attributes, relevant: %s, whole attributes: %s",
            chunk_id,
            len(chunk_results),
            relevant_data,
            chunk_results
        )
        for res in chunk_results:
            if res:
                all_discovery_results.append(res)

    merged_attributes = await merge_attribute_candidates(
        object_class=object_class,
        attribute_objects=all_discovery_results,
        job_id=job_id,
        build_dedup_chain=_build_dedupe_chain,
        chunk_id_doc_id_map=chunk_id_to_doc_id,
    )

    logger.info("[Digester:Attributes] Total unique attributes after merging: %d", len(merged_attributes))

    logger.info(
        "[Digester:Attributes] Final merged attributes for %s: %s",
        object_class,
        [attr.name for attr in merged_attributes],
    )

    logger.info("[Digester:Attributes] Complete objects: %s", merged_attributes)

    attributes_filtered_names = filter_ignored_attributes(merged_attributes)
    filtered_attributes = [attr for attr in merged_attributes if attr.name in attributes_filtered_names]
    removed_attributes = [attr.name for attr in merged_attributes if attr.name not in attributes_filtered_names]
    if removed_attributes:
        logger.info(
            "[Digester:Attributes] Removed %d ignored attributes during postprocessing: %s",
            len(removed_attributes),
            sorted(removed_attributes),
        )

    logger.info(
        "[Digester:Attributes] Filtered attributes for %s: %s",
        object_class,
        [attr.name for attr in filtered_attributes],
    )

    logger.info("[Digester:Attributes] Complete filtered objects: %s", json.dumps([attr.model_dump(exclude={'relevant_sequences', 'relevant_documentations'}) for attr in filtered_attributes], indent=2, ensure_ascii=False))

    builded_attributes = await build_attributes_from_sequences(filtered_attributes, object_class)

    logger.info("[Digester:Attributes] Attributes after building from sequences: %s", json.dumps([attr.model_dump(exclude={'relevant_sequences', 'relevant_documentations'}) for attr in builded_attributes], indent=2, ensure_ascii=False))

    if not builded_attributes:
        logger.error("[Digester:Attributes] No attributes left after building from sequences, returning empty result")
        await update_job_progress(job_id, stage=JobStage.failed, message="Attribute extraction complete with no attributes found")
        return {"result": {"attributes": {}}, "relevantDocumentations": []}

    consolidated_attributes = await consolidate_attributes(builded_attributes, object_class)

    logger.info("[Digester:Attributes] Attributes after final consolidation: %s", json.dumps(consolidated_attributes.model_dump(exclude={'attributes': {'__all__': {'relevant_documentations', 'relevant_sequences'}}}), indent=2, ensure_ascii=False))

    # Log attributes as a formatted table
    attributes_table = _format_attributes_as_table(consolidated_attributes.attributes)
    logger.info("[Digester:Attributes] Final attributes table for %s:\n%s", object_class, attributes_table)

    relevant_chunks = []
    seen_chunk_ids = set()
    for attr in consolidated_attributes.attributes.values():
        for chk in attr.relevant_documentations:
            if chk["chunk_id"] not in seen_chunk_ids:
                relevant_chunks.append({"chunkId": chk["chunk_id"], "docId": chk.get("doc_id", "unknown")})
                seen_chunk_ids.add(chk["chunk_id"])

    # for chunk_per_group, chunk_relevant in results:
    #     all_per_chunk.extend(chunk_per_group)
    #     relevant_chunks.extend(chunk_relevant)

    #     normalized_pairs = [normalize_chunk_pair(chunk_ref) for chunk_ref in chunk_relevant]
    #     valid_pairs = [pair for pair in normalized_pairs if pair is not None]
    #     if not valid_pairs:
    #         continue

    #     extracted_attribute_names = {
    #         str(attr_name).strip()
    #         for partial in chunk_per_group
    #         if isinstance(partial, dict)
    #         for attr_name in partial.keys()
    #         if isinstance(attr_name, str) and attr_name.strip()
    #     }
    #     for attr_name in extracted_attribute_names:
    #         seen_pairs = attribute_chunk_pairs.setdefault(attr_name, set())
    #         for doc_id, chunk_id in valid_pairs:
    #             seen_pairs.add((doc_id, chunk_id))

    # filled_attributes = await fill_missing_details(
    #     object_class=object_class,
    #     attributes=filtered_attributes,
    #     chunks=chunks,
    #     job_id=job_id,
    # )
    # postprocessed_attributes = normalize_readability_flags(filled_attributes)
    # attributes_with_references = _attach_relevant_documentations_per_attribute(
    #     postprocessed_attributes,
    #     attribute_chunk_pairs,
    # )

    await update_job_progress(job_id, stage=JobStage.schema_ready, message="Attribute extraction complete")

    return {"result": {"attributes": consolidated_attributes}, "relevantDocumentations": relevant_chunks}
