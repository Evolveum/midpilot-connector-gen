# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
SCIM 2.0 guided attributes extraction.

This module extracts ONLY custom attributes, unsupported attributes,
and deviations from standard SCIM attributes.
"""

import logging
from typing import Any, Dict, List
from uuid import UUID

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables.config import RunnableConfig

from .....common.jobs import increment_processed_documents, update_job_progress
from .....common.langfuse import langfuse_handler
from .....common.llm import get_default_llm, make_basic_chain
from ...prompts.scim.attributes_prompts import (
    scim_attributes_system_prompt,
    scim_attributes_user_prompt,
)
from ...schema import AttributeResponse
from ...scim.loader import get_base_scim_attributes, is_scim_standard_class

logger = logging.getLogger(__name__)


def _merge_custom_attributes(results: List[Dict[str, Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """
    Simple merge of custom attributes from multiple chunks.
    Later attributes override earlier ones if there are conflicts.
    """
    merged: Dict[str, Dict[str, Any]] = {}
    for result in results:
        merged.update(result)
    return merged


async def extract_scim_attributes(
    chunks: List[str],
    object_class: str,
    job_id: UUID,
    chunk_details: List[str] | None = None,
    doc_metadata_map: Dict[str, Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """
    Extract attributes for SCIM object class using guided approach:
    1. Load base SCIM attributes
    2. Extract custom attributes + deviations from docs
    3. Merge base + custom

    Args:
        chunks: List of documentation chunks to analyze
        object_class: Target object class name
        job_id: Job ID for progress tracking
        chunk_details: Optional list of document UUIDs for each chunk
        doc_metadata_map: Optional metadata mapping for documents

    Returns:
        Dictionary with:
        - "result": {"attributes": {...}} merged attributes
        - "relevantChunks": List of chunks with custom attributes
    """
    logger.info("[SCIM:Attributes] Starting guided extraction for %s", object_class)

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
        message=f"Extracting custom attributes for {object_class}",
    )

    all_custom_attributes: List[Dict[str, Dict[str, Any]]] = []
    relevant_chunks: List[Dict[str, Any]] = []

    for idx, (chunk, doc_uuid) in enumerate(zip(chunks, chunk_details, strict=False), 1):
        logger.info(
            "[SCIM:Attributes] Processing chunk %d/%d for %s (doc: %s)",
            idx,
            total_chunks,
            object_class,
            doc_uuid,
        )

        custom_attrs = await extract_custom_scim_attributes(
            chunk=chunk,
            object_class=object_class,
            job_id=job_id,
            base_attributes=base_attributes,
        )

        if custom_attrs:
            all_custom_attributes.append(custom_attrs)
            if doc_uuid:
                relevant_chunks.append({"docUuid": doc_uuid})

        await increment_processed_documents(job_id, delta=1)

    # Step 3: Merge custom attributes
    merged_custom = _merge_custom_attributes(all_custom_attributes)

    # Step 4: Merge base + custom
    final_attributes = base_attributes.copy()
    final_attributes.update(merged_custom)

    logger.info(
        "[SCIM:Attributes] Completed for %s. Total attributes: %d (base: %d, custom: %d)",
        object_class,
        len(final_attributes),
        len(base_attributes),
        len(merged_custom),
    )

    return {
        "result": {"attributes": final_attributes},
        "relevantChunks": relevant_chunks,
    }


async def extract_custom_scim_attributes(
    chunk: str,
    object_class: str,
    job_id: UUID,
    base_attributes: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """
    Extract ONLY custom attributes and deviations from a single chunk.

    Args:
        chunk: Documentation chunk to analyze
        object_class: Target object class name
        job_id: Job ID for progress tracking
        base_attributes: Base SCIM attributes for context

    Returns:
        Dictionary of custom attributes
    """
    # Format base attributes for LLM prompt
    formatted_base = _format_attributes_for_prompt(base_attributes)

    # Prepare prompts
    system_prompt = scim_attributes_system_prompt.replace("{object_class}", object_class).replace(
        "{scim_base_attributes}",
        f"Standard SCIM {object_class} attributes: {', '.join(base_attributes.keys()) if base_attributes else 'None (custom resource)'}",
    )

    user_prompt = (
        scim_attributes_user_prompt.replace("{object_class}", object_class)
        .replace("{formatted_base_attributes}", formatted_base)
        .replace("{chunk}", chunk)
    )

    # Call LLM
    llm = get_default_llm()
    parser: PydanticOutputParser[AttributeResponse] = PydanticOutputParser(pydantic_object=AttributeResponse)

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt + "\n\n{format_instructions}"),
            ("user", user_prompt),
        ]
    ).partial(format_instructions=parser.get_format_instructions())

    chain = make_basic_chain(
        prompt=prompt,
        llm=llm,
        parser=parser,
    )

    config = RunnableConfig(
        callbacks=[langfuse_handler] if langfuse_handler else [],
        run_name=f"SCIM Extract Custom Attributes - {object_class}",
        tags=["scim", "attributes", "custom", object_class.lower()],
    )

    try:
        result: AttributeResponse = await chain.ainvoke({}, config=config)
        attributes = result.attributes or {}

        if attributes:
            logger.info(
                "[SCIM:Attributes] Extracted %d custom/deviation attributes for %s",
                len(attributes),
                object_class,
            )

        return attributes  # type: ignore[return-value]

    except Exception as e:
        logger.error(
            "[SCIM:Attributes] Failed to extract custom attributes for %s: %s",
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
