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

from src.common.jobs import increment_processed_documents, update_job_progress
from src.common.langfuse import langfuse_handler
from src.common.llm import build_structured_chain
from src.common.utils.coerce import as_list
from src.common.utils.normalize import normalize_chunk_pair
from src.modules.digester.entities.attribute_filters import normalize_readability_flags
from src.modules.digester.extraction.llm_execution import invoke_llm
from src.modules.digester.extraction.metadata_helper import extract_summary_and_tags
from src.modules.digester.extractors.scim.object_class import build_embedded_object_class_name
from src.modules.digester.prompts.scim.attributes_prompts import (
    get_scim_attributes_system_prompt,
    get_scim_attributes_user_prompt,
)
from src.modules.digester.schemas import AttributeInfoScim, ExtractedAttributeResponseSCIM
from src.modules.digester.scim_baseline.loader import (
    get_base_scim_attributes,
    is_scim_standard_class,
    load_scim_base_schemas,
)

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
    # Format base attributes for prompt context
    formatted_base = _format_attributes_for_prompt(base_attributes)
    scim_base_summary = (
        f"Standard SCIM {object_class} attributes: {', '.join(base_attributes.keys())}"
        if base_attributes
        else "None (custom resource)"
    )

    return build_structured_chain(
        get_scim_attributes_system_prompt(),
        get_scim_attributes_user_prompt(),
        ExtractedAttributeResponseSCIM,
        partial_variables={
            "object_class": object_class,
            "scim_base_attributes": scim_base_summary,
            "formatted_base_attributes": formatted_base,
        },
    )


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
                existing_list = as_list(existing_refs)
                incoming_list = as_list(value)
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


def _map_scim_type_to_digester(scim_type: Any) -> str:
    type_map = {
        "string": "string",
        "boolean": "boolean",
        "decimal": "number",
        "integer": "integer",
        "dateTime": "string",
        "binary": "string",
        "reference": "string",
        "complex": "object",
    }
    return type_map.get(str(scim_type or ""), "string")


def _infer_scim_format(attr: Dict[str, Any]) -> Optional[str]:
    scim_type = attr.get("type")
    if scim_type == "dateTime":
        return "date-time"
    if scim_type == "binary":
        return "binary"
    if scim_type == "reference":
        return "reference"
    if scim_type == "complex":
        return "embedded"

    attr_name = str(attr.get("name") or "").lower()
    if "email" in attr_name:
        return "email"
    if "url" in attr_name or "uri" in attr_name:
        return "uri"
    return None


def _map_scim_mutability(attr: Dict[str, Any]) -> Tuple[bool, bool, bool]:
    mutability = str(attr.get("mutability") or "readWrite")
    if mutability == "readOnly":
        return False, False, True
    if mutability == "writeOnly":
        return True, True, False
    if mutability == "immutable":
        return False, True, True
    return True, True, True


def _map_scim_returned_by_default(attr: Dict[str, Any]) -> bool:
    returned = str(attr.get("returned") or "default")
    return returned in {"always", "default"}


def map_scim_attribute_to_digester_attribute(
    attr: Dict[str, Any],
    scim_path: str,
    *,
    attribute_type: Optional[str] = None,
    attribute_format: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Map one SCIM schema attribute or sub-attribute into AttributeInfoScim.
    """
    updatable, creatable, readable = _map_scim_mutability(attr)
    attribute = AttributeInfoScim(
        type=attribute_type or _map_scim_type_to_digester(attr.get("type")),
        format=attribute_format if attribute_format is not None else _infer_scim_format(attr),
        description=str(attr.get("description") or ""),
        mandatory=bool(attr.get("required", False)),
        updatable=updatable,
        creatable=creatable,
        readable=readable,
        multivalue=bool(attr.get("multiValued", False)),
        returnedByDefault=_map_scim_returned_by_default(attr),
        scimAttribute=scim_path,
    )
    return attribute.model_dump(by_alias=True)


def _get_schema_case_insensitive(schemas: Dict[str, Any], object_class: str) -> Optional[Dict[str, Any]]:
    normalized_name = object_class.strip().lower()
    for schema_name, schema in schemas.items():
        if schema_name.strip().lower() == normalized_name and isinstance(schema, dict):
            return schema
    return None


def _get_attribute_root(scim_path: str) -> str:
    """Return the first SCIM attribute segment for a path-like SCIM attribute."""
    normalized = str(scim_path or "").strip()
    if normalized.startswith("urn:"):
        return normalized

    match = re.match(r"([A-Za-z_$][A-Za-z0-9_$-]*)", normalized)
    return match.group(1) if match else normalized


def normalize_scim_path_for_lookup(scim_path: Any) -> str:
    """
    Normalize SCIM paths for schema-baseline lookup.

    Documentation often uses indexed or filtered multi-value paths such as
    `emails[0].value` or `emails[type eq 'work'].value`, while the SCIM schema
    baseline uses the canonical sub-attribute path `emails.value`.
    """
    normalized = str(scim_path or "").strip()
    if normalized.startswith("urn:"):
        return normalized.lower()
    return re.sub(r"\[[^\]]*\]", "", normalized).lower()


def get_scim_schema_attribute_context(object_class: str) -> Optional[Dict[str, set[str]]]:
    """
    Return top-level SCIM attribute names that help scope documented mappings.
    """
    schemas = load_scim_base_schemas()
    schema = _get_schema_case_insensitive(schemas, object_class)
    if schema is None:
        return None

    current_attributes: set[str] = set()
    complex_attributes: set[str] = set()
    other_standard_attributes: set[str] = set()

    attributes = schema.get("attributes", [])
    if isinstance(attributes, list):
        for attr in attributes:
            if not isinstance(attr, dict):
                continue
            attr_name = attr.get("name")
            if not isinstance(attr_name, str) or not attr_name.strip():
                continue
            normalized_name = attr_name.strip().lower()
            current_attributes.add(normalized_name)
            if attr.get("type") == "complex":
                complex_attributes.add(normalized_name)

    normalized_object_class = object_class.strip().lower()
    for schema_name, other_schema in schemas.items():
        if schema_name.strip().lower() == normalized_object_class or not isinstance(other_schema, dict):
            continue
        other_attributes = other_schema.get("attributes", [])
        if not isinstance(other_attributes, list):
            continue
        for attr in other_attributes:
            if not isinstance(attr, dict):
                continue
            attr_name = attr.get("name")
            if isinstance(attr_name, str) and attr_name.strip():
                other_standard_attributes.add(attr_name.strip().lower())

    return {
        "current_attributes": current_attributes,
        "complex_attributes": complex_attributes,
        "other_standard_attributes": other_standard_attributes,
    }


def get_scim_complex_attribute_reference_type(
    scim_path: str,
    attribute_context: Dict[str, set[str]],
    object_class: str,
) -> Optional[str]:
    """
    Return embedded object-class name for a SCIM path rooted in a complex attribute.
    """
    root = _get_attribute_root(scim_path).strip()
    if not root or root.startswith("urn:"):
        return None
    if root.lower() not in attribute_context["complex_attributes"]:
        return None
    return build_embedded_object_class_name(object_class, root)


def scim_path_targets_filtered_attribute(scim_path: str, attribute_context: Dict[str, set[str]]) -> bool:
    """
    True when a documented mapping belongs to another SCIM object class/scope.
    """
    root = _get_attribute_root(scim_path).strip().lower()
    if not root or root.startswith("urn:"):
        return False

    if root in attribute_context["other_standard_attributes"] and root not in attribute_context["current_attributes"]:
        return True

    return False


def _find_embedded_source_attribute(
    schemas: Dict[str, Any],
    object_class: str,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    normalized_name = object_class.strip().lower()
    for parent_class, schema in schemas.items():
        if not isinstance(schema, dict):
            continue
        attributes = schema.get("attributes", [])
        if not isinstance(attributes, list):
            continue
        for attr in attributes:
            if not isinstance(attr, dict) or attr.get("type") != "complex":
                continue
            attr_name = attr.get("name")
            if not isinstance(attr_name, str) or not attr_name.strip():
                continue
            embedded_name = build_embedded_object_class_name(parent_class, attr_name)
            if embedded_name.strip().lower() == normalized_name:
                return attr_name, attr
    return None


def get_scim_schema_attributes_for_object_class(object_class: str) -> Optional[Dict[str, Dict[str, Any]]]:
    """
    Return deterministic SCIM attributes for a standard or embedded object class.

    None means the object class is not backed by the local SCIM base schemas and
    should be handled by the custom/documentation extraction path.
    """
    schemas = load_scim_base_schemas()

    schema = _get_schema_case_insensitive(schemas, object_class)
    if schema is not None:
        result: Dict[str, Dict[str, Any]] = {}
        attributes = schema.get("attributes", [])
        if not isinstance(attributes, list):
            return result
        for attr in attributes:
            if not isinstance(attr, dict):
                continue
            attr_name = attr.get("name")
            if not isinstance(attr_name, str) or not attr_name.strip():
                continue
            if attr.get("type") == "complex":
                result[attr_name] = map_scim_attribute_to_digester_attribute(
                    attr,
                    attr_name,
                    attribute_type=build_embedded_object_class_name(object_class, attr_name),
                    attribute_format="embedded",
                )
                continue
            result[attr_name] = map_scim_attribute_to_digester_attribute(attr, attr_name)
        return result

    embedded_source = _find_embedded_source_attribute(schemas, object_class)
    if embedded_source is None:
        return None

    source_attr_name, source_attr = embedded_source
    result = {}
    sub_attributes = source_attr.get("subAttributes", [])
    if not isinstance(sub_attributes, list):
        return result
    for sub_attr in sub_attributes:
        if not isinstance(sub_attr, dict):
            continue
        sub_attr_name = sub_attr.get("name")
        if not isinstance(sub_attr_name, str) or not sub_attr_name.strip():
            continue
        scim_path = f"{source_attr_name}.{sub_attr_name}"
        result[sub_attr_name] = map_scim_attribute_to_digester_attribute(sub_attr, scim_path)
    return result
