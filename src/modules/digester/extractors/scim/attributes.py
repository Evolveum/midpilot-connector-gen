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
from typing import Any, Dict, List, Optional
from uuid import UUID

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

from .....common.jobs import increment_processed_documents, update_job_progress
from .....common.langfuse import langfuse_handler
from .....common.llm import get_default_llm, make_basic_chain
from ...prompts.scim.attributes_prompts import (
    get_scim_attributes_system_prompt,
    get_scim_attributes_user_prompt,
)
from ...schema import AttributeResponse
from ...scim.loader import get_base_scim_attributes, is_scim_standard_class
from ...utils.metadata_helper import extract_summary_and_tags

logger = logging.getLogger(__name__)


def _build_scim_attribute_chain(object_class: str, base_attributes: Dict[str, Dict[str, Any]]) -> Any:
    """
    Build the LLM chain for extracting custom SCIM attributes from a single chunk.

    Args:
        object_class: Name of the SCIM object class
        base_attributes: Base SCIM attributes for context

    Returns:
        Configured LangChain runnable
    """
    parser: PydanticOutputParser[AttributeResponse] = PydanticOutputParser(pydantic_object=AttributeResponse)
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
    doc_metadata_map: Dict[str, Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """
    Extract application-to-SCIM attribute mappings for SCIM object class.

    Args:
        chunks: List of documentation chunks to analyze
        object_class: Target object class name
        job_id: Job ID for progress tracking
        chunk_details: Optional list of document UUIDs for each chunk
        doc_metadata_map: Optional metadata mapping for documents

    Returns:
        Dictionary with:
        - "result": {"attributes": {...}} mapped attributes
        - "relevantChunks": List of chunks with mapping evidence
    """
    logger.info("[SCIM:Attributes] Starting mapping extraction for %s", object_class)

    if chunk_details is None:
        chunk_details = [""] * len(chunks)

    # Step 1: Load base SCIM attributes (if standard class)
    base_attributes = {}
    if is_scim_standard_class(object_class):
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

    # Create parallel tasks for all chunks
    tasks = []
    for chunk, doc_uuid in zip(chunks, chunk_details, strict=False):
        # Get metadata for this document if available
        doc_metadata = doc_metadata_map.get(str(doc_uuid)) if doc_metadata_map and doc_uuid else None

        tasks.append(
            extract_custom_scim_attributes(
                chain=chain,
                chunk=chunk,
                object_class=object_class,
                doc_metadata=doc_metadata,
            )
        )

    # Execute all tasks in parallel
    all_results = list(await asyncio.gather(*tasks))
    if total_chunks:
        await increment_processed_documents(job_id, delta=total_chunks)

    # Collect results and relevant chunks
    all_custom_attributes: List[Dict[str, Dict[str, Any]]] = []
    relevant_chunks: List[Dict[str, Any]] = []

    for custom_attrs, doc_uuid in zip(all_results, chunk_details, strict=False):
        if custom_attrs:
            all_custom_attributes.append(custom_attrs)
            if doc_uuid:
                relevant_chunks.append({"docUuid": doc_uuid})

    logger.info(
        "[SCIM:Attributes] Completed parallel processing. Found mappings in %d/%d chunks",
        len(all_custom_attributes),
        total_chunks,
    )

    # Step 3: Merge custom attributes
    merged_custom = _merge_custom_attributes(all_custom_attributes)

    logger.info(
        "[SCIM:Attributes] Completed for %s. Total mappings: %d (base reference attrs: %d)",
        object_class,
        len(merged_custom),
        len(base_attributes),
    )

    return {
        "result": {"attributes": merged_custom},
        "relevantChunks": relevant_chunks,
    }


async def extract_custom_scim_attributes(
    chain: Any,
    chunk: str,
    object_class: str,
    doc_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Extract attribute mappings from a single chunk using a pre-built chain.

    Args:
        chain: Pre-configured LLM chain for extraction
        chunk: Documentation chunk to analyze
        object_class: Target object class name
        doc_metadata: Optional metadata for the document

    Returns:
        Dictionary of mapped attributes (application name -> AttributeInfo)
    """
    try:
        # Extract summary and tags from doc metadata
        summary, tags = extract_summary_and_tags(doc_metadata)

        result = await chain.ainvoke(
            {
                "chunk": chunk,
                "summary": summary,
                "tags": tags,
            },
            config={"callbacks": [langfuse_handler] if langfuse_handler else []},
        )

        if isinstance(result, AttributeResponse):
            attributes = result.attributes or {}
        elif isinstance(result, dict):
            parsed = AttributeResponse.model_validate(result)
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
