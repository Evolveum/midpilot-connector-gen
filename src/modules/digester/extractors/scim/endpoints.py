# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
SCIM 2.0 guided endpoints extraction.

This module extracts ONLY custom endpoints, unsupported endpoints,
and deviations from standard SCIM endpoints.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

from .....common.database.config import async_session_maker
from .....common.database.repositories.session_repository import SessionRepository
from .....common.jobs import increment_processed_documents, update_job_progress
from .....common.langfuse import langfuse_handler
from .....common.llm import get_default_llm, make_basic_chain
from ...prompts.scim.endpoints_prompts import (
    scim_endpoints_system_prompt,
    scim_endpoints_user_prompt,
)
from ...schema import EndpointInfo, EndpointResponse
from ...scim.loader import generate_scim_crud_endpoints, get_base_scim_endpoints, is_scim_standard_class
from ...utils.metadata_helper import extract_summary_and_tags
from ...utils.scim_resource import extract_scim_resource_path, infer_scim_resource_path

logger = logging.getLogger(__name__)


async def pregenerate_scim_endpoints(
    *,
    session_id: UUID,
    object_class: str,
    base_api_url: str,
    job_id: UUID,
    relevant_chunks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Generate SCIM endpoints deterministically from object class/resource mapping.
    """
    await update_job_progress(
        job_id,
        total_processing=1,
        processing_completed=0,
        message=f"Pregenerating SCIM endpoints for {object_class}",
    )

    object_class_data: Dict[str, Any] = {}
    try:
        async with async_session_maker() as db:
            repo = SessionRepository(db)
            object_classes_output = await repo.get_session_data(session_id, "objectClassesOutput")
            if object_classes_output and isinstance(object_classes_output, dict):
                object_classes = object_classes_output.get("objectClasses", [])
                if isinstance(object_classes, list):
                    normalized_name = object_class.strip().lower()
                    for obj_class in object_classes:
                        if isinstance(obj_class, dict) and obj_class.get("name", "").strip().lower() == normalized_name:
                            object_class_data = obj_class
                            break
    except Exception as e:
        logger.warning("[SCIM:Endpoints] Failed to read objectClassesOutput for pregeneration: %s", e)

    endpoints: List[Dict[str, Any]]
    if is_scim_standard_class(object_class):
        endpoints = get_base_scim_endpoints(object_class, base_api_url)
    else:
        resource_path = extract_scim_resource_path(object_class_data) or infer_scim_resource_path(object_class)
        endpoints = generate_scim_crud_endpoints(resource_path, object_class)

    await increment_processed_documents(job_id, delta=1)

    logger.info(
        "[SCIM:Endpoints] Pregenerated %d endpoints for %s",
        len(endpoints),
        object_class,
    )

    return {
        "result": {"endpoints": endpoints},
        "relevantDocumentations": relevant_chunks,
    }


def _build_scim_endpoint_chain(object_class: str, base_api_url: str, base_endpoints: List[Dict[str, Any]]) -> Any:
    """
    Build the LLM chain for extracting custom SCIM endpoints from a single chunk.

    Args:
        object_class: Name of the SCIM object class
        base_api_url: Base API URL
        base_endpoints: Base SCIM endpoints for context

    Returns:
        Configured LangChain runnable
    """
    parser: PydanticOutputParser[EndpointResponse] = PydanticOutputParser(pydantic_object=EndpointResponse)
    llm = get_default_llm()

    formatted_base = _format_endpoints_for_prompt(base_endpoints)
    base_summary = (
        f"Standard SCIM {object_class} endpoints:\n{formatted_base if base_endpoints else 'None (custom resource)'}"
    )

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", scim_endpoints_system_prompt + "\n\n{format_instructions}"),
            ("user", scim_endpoints_user_prompt),
        ]
    ).partial(
        object_class=object_class,
        base_api_url=base_api_url or "{base_api_url}",
        scim_base_endpoints=base_summary,
        formatted_base_endpoints=formatted_base if base_endpoints else "None (custom resource)",
        format_instructions=parser.get_format_instructions(),
    )

    return make_basic_chain(prompt, llm, parser)


async def extract_scim_endpoints(
    chunks: List[str],
    object_class: str,
    job_id: UUID,
    base_api_url: str = "",
    chunk_details: List[str] | None = None,
    doc_metadata_map: Dict[str, Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """
    Extract endpoints for SCIM object class using guided approach:
    1. Return base SCIM endpoints
    2. Extract custom endpoints + deviations from docs
    3. Merge base + custom

    Args:
        chunks: List of documentation chunks to analyze
        object_class: Target object class name
        job_id: Job ID for progress tracking
        base_api_url: Base API URL for endpoint paths
        chunk_details: Optional list of document UUIDs for each chunk
        doc_metadata_map: Optional metadata mapping for documents

    Returns:
        Dictionary with:
        - "result": {"endpoints": [...]} merged endpoints
        - "relevantDocumentations": List of chunks with custom endpoints
    """
    logger.info("[SCIM:Endpoints] Starting guided extraction for %s", object_class)

    if chunk_details is None:
        chunk_details = [""] * len(chunks)

    # Step 1: Load base SCIM endpoints (if standard class)
    base_endpoints_data = []
    if is_scim_standard_class(object_class):
        base_endpoints_data = get_base_scim_endpoints(object_class, base_api_url)
        logger.info(
            "[SCIM:Endpoints] Loaded %d base endpoints for %s",
            len(base_endpoints_data),
            object_class,
        )
    else:
        logger.info(
            "[SCIM:Endpoints] %s is not a standard SCIM class, skipping base endpoints",
            object_class,
        )

    # Convert to EndpointInfo objects
    base_endpoints = [
        EndpointInfo(
            path=ep["path"],
            method=ep["method"],
            description=ep["description"],
            response_content_type=ep.get("responseContentType"),
            request_content_type=ep.get("requestContentType"),
            suggested_use=ep.get("suggestedUse", []),
        )
        for ep in base_endpoints_data
    ]

    # Step 2: Extract custom endpoints and deviations from documentation
    total_chunks = len(chunks)
    await update_job_progress(
        job_id,
        total_processing=total_chunks,
        processing_completed=0,
        message=f"Extracting custom endpoints for {object_class}",
    )

    chain = _build_scim_endpoint_chain(object_class, base_api_url, base_endpoints_data)

    logger.info(
        "[SCIM:Endpoints] Processing %d chunks in parallel for %s",
        total_chunks,
        object_class,
    )

    tasks = []
    for chunk, doc_uuid in zip(chunks, chunk_details, strict=False):
        doc_metadata = doc_metadata_map.get(str(doc_uuid)) if doc_metadata_map and doc_uuid else None
        tasks.append(
            extract_custom_scim_endpoints(
                chain=chain,
                chunk=chunk,
                object_class=object_class,
                doc_metadata=doc_metadata,
            )
        )

    all_results = list(await asyncio.gather(*tasks))
    if total_chunks:
        await increment_processed_documents(job_id, delta=total_chunks)

    all_custom_endpoints: List[List[EndpointInfo]] = []
    relevant_chunks: List[Dict[str, Any]] = []
    for custom_eps, doc_uuid in zip(all_results, chunk_details, strict=False):
        if custom_eps:
            all_custom_endpoints.append(custom_eps)
            if doc_uuid:
                relevant_chunks.append({"docUuid": doc_uuid})

    # Step 3: Flatten and merge custom endpoints
    flat_custom_endpoints = [ep for sublist in all_custom_endpoints for ep in sublist]

    # Step 4: Merge base + custom
    all_endpoints = base_endpoints + flat_custom_endpoints

    logger.info(
        "[SCIM:Endpoints] Completed for %s. Total endpoints: %d (base: %d, custom: %d)",
        object_class,
        len(all_endpoints),
        len(base_endpoints),
        len(flat_custom_endpoints),
    )

    return {
        "result": {"endpoints": [ep.model_dump(by_alias=True) for ep in all_endpoints]},
        "relevantDocumentations": relevant_chunks,
    }


async def extract_custom_scim_endpoints(
    chain: Any,
    chunk: str,
    object_class: str,
    doc_metadata: Optional[Dict[str, Any]] = None,
) -> List[EndpointInfo]:
    """
    Extract ONLY custom endpoints and deviations from a single chunk.

    Args:
        chain: Pre-configured LLM chain for extraction
        chunk: Documentation chunk to analyze
        object_class: Target object class name
        doc_metadata: Optional metadata for the document

    Returns:
        List of custom EndpointInfo objects
    """
    try:
        summary, tags = extract_summary_and_tags(doc_metadata)

        result = await chain.ainvoke(
            {
                "chunk": chunk,
                "summary": summary,
                "tags": tags,
            },
            config={"callbacks": [langfuse_handler] if langfuse_handler else []},
        )

        if isinstance(result, EndpointResponse):
            endpoints = result.endpoints or []
        elif isinstance(result, dict):
            parsed = EndpointResponse.model_validate(result)
            endpoints = parsed.endpoints or []
        else:
            logger.warning("[SCIM:Endpoints] Unexpected result type: %s", type(result))
            return []

        if endpoints:
            logger.info(
                "[SCIM:Endpoints] Extracted %d custom/deviation endpoints for %s",
                len(endpoints),
                object_class,
            )

        return endpoints

    except Exception as e:
        logger.error(
            "[SCIM:Endpoints] Failed to extract custom endpoints for %s: %s",
            object_class,
            e,
        )
        return []


def _format_endpoints_for_prompt(endpoints: List[Dict[str, Any]]) -> str:
    """Format base endpoints for inclusion in LLM prompt."""
    if not endpoints:
        return "None"

    lines = []
    for ep in endpoints:
        method = ep.get("method", "?")
        path = ep.get("path", "?")
        desc = ep.get("description", "")[:80]  # Truncate for brevity
        lines.append(f"  - {method} {path} - {desc}")

    return "\n".join(lines)
