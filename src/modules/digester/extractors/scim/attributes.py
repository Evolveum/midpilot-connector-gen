# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
SCIM 2.0 guided attributes extraction.

This module extracts explicit application-to-SCIM attribute mappings.
"""

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

from src.common.jobs import increment_processed_documents, update_job_progress
from src.common.langfuse import langfuse_handler
from src.common.llm import get_default_llm, make_basic_chain
from src.common.utils.normalize import normalize_chunk_pair
from src.modules.digester.prompts.scim.attributes_prompts import (
    get_scim_attributes_system_prompt,
    get_scim_attributes_user_prompt,
)
from src.modules.digester.schema import ExtractedAttributeResponseSCIM
from src.modules.digester.scim.attributes import (
    get_scim_complex_attribute_reference_type,
    get_scim_schema_attribute_context,
    get_scim_schema_attributes_for_object_class,
    normalize_scim_path_for_lookup,
    scim_path_targets_filtered_attribute,
)
from src.modules.digester.scim.loader import get_base_scim_attributes, is_scim_standard_class
from src.modules.digester.utils.attribute_filters import normalize_readability_flags
from src.modules.digester.utils.llm_execution import invoke_llm
from src.modules.digester.utils.metadata_helper import extract_summary_and_tags

logger = logging.getLogger(__name__)


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


def _build_scim_attribute_chain(object_class: str, base_attributes: Dict[str, Dict[str, Any]]) -> Any:
    """
    Build the LLM chain for extracting custom SCIM attributes from a single chunk.

    Args:
        object_class: Name of the SCIM object class
        base_attributes: Base SCIM attributes for context

    Returns:
        Configured LangChain runnable
    """
    parser: PydanticOutputParser[ExtractedAttributeResponseSCIM] = PydanticOutputParser(
        pydantic_object=ExtractedAttributeResponseSCIM
    )
    llm = get_default_llm()

    # Format base attributes for prompt context
    formatted_base = _format_attributes_for_prompt(base_attributes)
    scim_base_summary = (
        f"Standard SCIM {object_class} attributes: {', '.join(base_attributes.keys())}"
        if base_attributes
        else "None (custom resource)"
    )

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", get_scim_attributes_system_prompt() + "\n\n{format_instructions}"),
            ("user", get_scim_attributes_user_prompt()),
        ]
    ).partial(
        object_class=object_class,
        scim_base_attributes=scim_base_summary,
        formatted_base_attributes=formatted_base,
        format_instructions=parser.get_format_instructions(),
    )

    return make_basic_chain(prompt, llm, parser)


def _merge_custom_attributes(results: List[Dict[str, Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """
    Merge mapped attributes from multiple chunks.
    Later values fill gaps from earlier values.
    """
    merged: Dict[str, Dict[str, Any]] = {}

    for result in results:
        for attr_name, attr_info in result.items():
            existing = merged.get(attr_name, {})
            merged_info = dict(existing)

            for key, value in attr_info.items():
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                merged_info[key] = value

            merged[attr_name] = merged_info

    return merged


def _merge_schema_attributes_with_documented_mappings(
    schema_attributes: Dict[str, Dict[str, Any]],
    documented_attributes: Dict[str, Dict[str, Any]],
    *,
    include_unmatched_mappings: bool,
    attribute_context: Dict[str, set[str]] | None = None,
    object_class: str | None = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Merge documented target-app SCIM mappings over deterministic schema attributes.

    The SCIM schema remains the baseline. Documented mappings enrich matching
    schema attributes by `scimAttribute`; custom mappings that do not match the
    baseline are appended under the application/native attribute name produced
    by the LLM.
    """
    merged = {attr_name: dict(attr_info) for attr_name, attr_info in schema_attributes.items()}
    scim_path_to_name: Dict[str, str] = {}
    for attr_name, attr_info in merged.items():
        scim_path_to_name[normalize_scim_path_for_lookup(attr_name)] = attr_name
        scim_path_to_name[normalize_scim_path_for_lookup(attr_info.get("scimAttribute") or attr_name)] = attr_name

    for documented_name, documented_info in documented_attributes.items():
        info = dict(documented_info)
        scim_attribute = info.get("scimAttribute")
        scim_path_raw = str(scim_attribute or documented_name).strip()
        scim_path = normalize_scim_path_for_lookup(scim_path_raw)
        baseline_name = scim_path_to_name.get(scim_path)

        if baseline_name is None:
            if attribute_context and object_class:
                embedded_type = get_scim_complex_attribute_reference_type(
                    scim_path_raw,
                    attribute_context,
                    object_class,
                )
                if embedded_type:
                    info["type"] = embedded_type
                    info["format"] = "embedded"
                    merged[documented_name] = info
                    continue

            if attribute_context and scim_path_targets_filtered_attribute(scim_path, attribute_context):
                logger.info(
                    "[SCIM:Attributes] Filtered documented mapping '%s' for SCIM path '%s' because it belongs to a different SCIM object class",
                    documented_name,
                    scim_attribute,
                )
                continue
            if include_unmatched_mappings:
                merged[documented_name] = info
            continue

        documented_key = str(documented_name).strip()
        target_name = documented_key or baseline_name
        baseline_info = dict(merged.get(baseline_name, {}))
        if target_name != baseline_name:
            existing_target = merged.get(target_name)
            if isinstance(existing_target, dict):
                baseline_info.update(existing_target)
            merged.pop(baseline_name, None)

        baseline_scim_attribute = baseline_info.get("scimAttribute") or scim_attribute or baseline_name
        embedded_type = None
        if attribute_context and object_class:
            embedded_type = get_scim_complex_attribute_reference_type(
                str(baseline_scim_attribute),
                attribute_context,
                object_class,
            )
        for key, value in info.items():
            if key == "scimAttribute":
                continue
            if embedded_type and key in {"type", "format"}:
                continue
            if key == "relevantDocumentations":
                existing_refs = baseline_info.get("relevantDocumentations")
                existing_list = existing_refs if isinstance(existing_refs, list) else []
                incoming_list = value if isinstance(value, list) else []
                seen = {
                    (str(item.get("docId") or item.get("doc_id")), str(item.get("chunkId") or item.get("chunk_id")))
                    for item in existing_list
                    if isinstance(item, dict)
                }
                for item in incoming_list:
                    if not isinstance(item, dict):
                        continue
                    key_pair = (
                        str(item.get("docId") or item.get("doc_id")),
                        str(item.get("chunkId") or item.get("chunk_id")),
                    )
                    if key_pair in seen:
                        continue
                    existing_list.append(item)
                    seen.add(key_pair)
                baseline_info["relevantDocumentations"] = existing_list
                continue
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            baseline_info[key] = value

        baseline_info["scimAttribute"] = baseline_scim_attribute
        if embedded_type:
            baseline_info["type"] = embedded_type
            baseline_info["format"] = "embedded"
        merged[target_name] = baseline_info
        scim_path_to_name[scim_path] = target_name
        scim_path_to_name[normalize_scim_path_for_lookup(target_name)] = target_name

    return merged


def _normalize_attribute_name(name: str) -> str:
    """Normalize mapped attribute name key for stable deduplication."""
    return " ".join(str(name).split()).strip()


def _infer_scim_attribute_from_description(description: Optional[str]) -> Optional[str]:
    """Best-effort extraction of SCIM path from mapping description."""
    if not description:
        return None

    patterns = [
        r"SCIM\s*[`'\":]?\s*([A-Za-z0-9_.\[\]\-]+)",
        r"maps\s+to\s+([A-Za-z0-9_.\[\]\-]+)",
        r"mapped\s+from\s+([A-Za-z0-9_.\[\]\-]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, description, flags=re.IGNORECASE)
        if match:
            candidate = match.group(1).strip().strip("`'\"").rstrip(".,;:)]}")
            if candidate:
                return candidate

    return None


async def extract_scim_attributes(
    chunks: List[str],
    object_class: str,
    job_id: UUID,
    chunk_details: List[str] | None = None,
    chunk_metadata_map: Dict[str, Dict[str, Any]] | None = None,
    chunk_id_to_doc_id: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    """
    Extract application-to-SCIM attribute mappings for SCIM object class.

    Args:
        chunks: List of documentation chunks to analyze
        object_class: Target object class name
        job_id: Job ID for progress tracking
        chunk_details: Optional list of chunk IDs for each chunk
        chunk_metadata_map: Optional metadata mapping for chunks
        chunk_id_to_doc_id: Optional mapping of chunk ID to doc ID

    Returns:
        Dictionary with:
        - "result": {"attributes": {...}} mapped attributes
        - "relevantDocumentations": List of chunks with mapping evidence
    """
    logger.info("[SCIM:Attributes] Starting mapping extraction for %s", object_class)

    if chunk_details is None:
        chunk_details = [""] * len(chunks)

    schema_attributes = get_scim_schema_attributes_for_object_class(object_class)
    if schema_attributes is not None and not chunks:
        await update_job_progress(
            job_id,
            total_processing=1,
            processing_completed=0,
            message=f"Deriving SCIM attributes for {object_class}",
        )
        await increment_processed_documents(job_id, delta=1)
        logger.info(
            "[SCIM:Attributes] Derived %d attributes for %s from SCIM schema heuristics",
            len(schema_attributes),
            object_class,
        )
        return {
            "result": {"attributes": schema_attributes},
            "relevantDocumentations": [],
        }

    # Step 1: Load base SCIM attributes for LLM context when schema heuristics are unavailable.
    base_attributes = schema_attributes or {}
    has_schema_baseline = schema_attributes is not None
    is_standard_class = is_scim_standard_class(object_class)
    if has_schema_baseline:
        logger.info(
            "[SCIM:Attributes] Using %d schema baseline attributes for %s",
            len(base_attributes),
            object_class,
        )
    elif is_standard_class:
        if not base_attributes:
            base_attributes = get_base_scim_attributes(object_class)
        logger.info(
            "[SCIM:Attributes] Loaded %d base attributes for %s",
            len(base_attributes),
            object_class,
        )
    else:
        logger.info(
            "[SCIM:Attributes] %s is not a standard SCIM class, skipping base attributes",
            object_class,
        )

    # Step 2: Extract custom attributes and deviations from documentation
    total_chunks = len(chunks)
    await update_job_progress(
        job_id,
        total_processing=total_chunks,
        processing_completed=0,
        message=f"Extracting SCIM attribute mappings for {object_class}",
    )

    # Build extraction chain once for all chunks
    chain = _build_scim_attribute_chain(object_class, base_attributes)

    logger.info(
        "[SCIM:Attributes] Processing %d chunks in parallel for %s",
        total_chunks,
        object_class,
    )

    tasks = []
    for chunk, chunk_id in zip(chunks, chunk_details, strict=False):
        chunk_metadata = chunk_metadata_map.get(str(chunk_id)) if chunk_metadata_map and chunk_id else None

        tasks.append(
            extract_custom_scim_attributes(
                chain=chain,
                chunk=chunk,
                object_class=object_class,
                chunk_metadata=chunk_metadata,
            )
        )

    # Execute all tasks in parallel
    all_results = list(await asyncio.gather(*tasks))
    if total_chunks:
        await increment_processed_documents(job_id, delta=total_chunks)

    # Collect results and relevant chunks
    all_custom_attributes: List[Dict[str, Dict[str, Any]]] = []
    relevant_chunks: List[Dict[str, Any]] = []
    attribute_chunk_pairs: Dict[str, Set[Tuple[str, str]]] = {}

    for custom_attrs, chunk_id in zip(all_results, chunk_details, strict=False):
        if custom_attrs:
            all_custom_attributes.append(custom_attrs)
            chunk_pair: Optional[Tuple[str, str]] = None
            if chunk_id:
                chunk_id_str = str(chunk_id)
                doc_id = chunk_id_to_doc_id.get(chunk_id_str) if chunk_id_to_doc_id else None
                if doc_id:
                    chunk_ref = {"doc_id": doc_id, "chunk_id": chunk_id_str}
                    relevant_chunks.append(chunk_ref)
                    chunk_pair = normalize_chunk_pair(chunk_ref)
                else:
                    logger.warning(
                        "[SCIM:Attributes] Missing docId for chunk %s, skipping relevant chunk mapping",
                        chunk_id_str,
                    )
            if chunk_pair:
                for attr_name in custom_attrs.keys():
                    seen_pairs = attribute_chunk_pairs.setdefault(str(attr_name), set())
                    seen_pairs.add(chunk_pair)

    logger.info(
        "[SCIM:Attributes] Completed parallel processing. Found mappings in %d/%d chunks",
        len(all_custom_attributes),
        total_chunks,
    )

    # Step 3: Merge custom attributes
    merged_custom = _merge_custom_attributes(all_custom_attributes)
    postprocessed_custom = normalize_readability_flags(merged_custom)
    merged_custom_with_references = _attach_relevant_documentations_per_attribute(
        postprocessed_custom,
        attribute_chunk_pairs,
    )

    if schema_attributes is not None:
        merged_custom_with_references = _merge_schema_attributes_with_documented_mappings(
            schema_attributes,
            merged_custom_with_references,
            include_unmatched_mappings=is_standard_class,
            attribute_context=get_scim_schema_attribute_context(object_class) if is_standard_class else None,
            object_class=object_class,
        )

    logger.info(
        "[SCIM:Attributes] Completed for %s. Total attributes: %d (schema baseline: %d, documented mappings: %d)",
        object_class,
        len(merged_custom_with_references),
        len(schema_attributes or {}),
        len(merged_custom),
    )

    return {
        "result": {"attributes": merged_custom_with_references},
        "relevantDocumentations": relevant_chunks,
    }


async def extract_custom_scim_attributes(
    chain: Any,
    chunk: str,
    object_class: str,
    chunk_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Extract attribute mappings from a single chunk using a pre-built chain.

    Args:
        chain: Pre-configured LLM chain for extraction
        chunk: Documentation chunk to analyze
        object_class: Target object class name
        chunk_metadata: Optional metadata for the chunk

    Returns:
        Dictionary of mapped attributes (application name -> AttributeInfo)
    """
    try:
        summary, tags = extract_summary_and_tags(chunk_metadata)

        result = await invoke_llm(
            chain,
            {
                "chunk": chunk,
                "summary": summary,
                "tags": tags,
            },
            config={"callbacks": [langfuse_handler] if langfuse_handler else []},
        )

        if isinstance(result, ExtractedAttributeResponseSCIM):
            attributes = result.attributes or {}
        elif isinstance(result, dict):
            parsed = ExtractedAttributeResponseSCIM.model_validate(result)
            attributes = parsed.attributes or {}
        else:
            logger.warning("[SCIM:Attributes] Unexpected result type: %s", type(result))
            return {}

        if attributes:
            logger.info(
                "[SCIM:Attributes] Extracted %d raw mapping candidates for %s",
                len(attributes),
                object_class,
            )

        mapped_attributes: Dict[str, Dict[str, Any]] = {}

        for raw_name, info in attributes.items():
            normalized_name = _normalize_attribute_name(raw_name)
            if not normalized_name:
                continue

            info_dict = info.model_dump()
            scim_attribute = info_dict.get("scimAttribute")
            if isinstance(scim_attribute, str):
                scim_attribute = scim_attribute.strip()

            if not scim_attribute:
                scim_attribute = _infer_scim_attribute_from_description(info_dict.get("description"))

            if not scim_attribute:
                # Keep only explicit mapping records.
                continue

            info_dict["scimAttribute"] = scim_attribute
            mapped_attributes[normalized_name] = info_dict

        if mapped_attributes:
            logger.info(
                "[SCIM:Attributes] Accepted %d mapping attributes for %s",
                len(mapped_attributes),
                object_class,
            )

        return mapped_attributes

    except Exception as e:
        logger.error(
            "[SCIM:Attributes] Failed to extract mapping attributes for %s: %s",
            object_class,
            e,
        )
        return {}


def _format_attributes_for_prompt(attributes: Dict[str, Dict[str, Any]]) -> str:
    """Format base attributes for inclusion in LLM prompt."""
    if not attributes:
        return "None (this is a custom resource type)"

    lines = []
    for attr_name, attr_info in list(attributes.items())[:30]:  # Limit for prompt size
        type_str = attr_info.get("type", "string")
        required = " (REQUIRED)" if attr_info.get("mandatory") else ""
        readonly = " (read-only)" if not attr_info.get("updatable") else ""
        multivalue = " (multi-valued)" if attr_info.get("multivalue") else ""

        lines.append(f"  - {attr_name}: {type_str}{required}{readonly}{multivalue}")

    if len(attributes) > 30:
        lines.append(f"  ... and {len(attributes) - 30} more attributes")

    return "\n".join(lines)
