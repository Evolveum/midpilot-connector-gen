# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Endpoint extraction workflow.

Owns the entity-level flow: resolve the effective protocol and dispatch to the
REST/SCIM/SQL leaf extractors, retry with broader documentation criteria when the
endpoint-focused chunks yield no endpoints, and persist the extracted endpoints back
onto the object class. Protocol-specific leaves live under ``extractors/rest``,
``extractors/scim`` and ``extractors/sql``.
"""

import logging
from collections.abc import Mapping
from typing import Any, Dict, List
from uuid import UUID

from src.common.chunk_filter.filter import filter_documentation_items
from src.common.documentation.content_types import is_conndev_documentation_item
from src.common.enums import ApiType
from src.common.jobs import update_job_progress
from src.common.utils.session_info_metadata import resolve_effective_api_type
from src.modules.digester.entities.object_classes import build_endpoint_result, extract_endpoints_from_result
from src.modules.digester.extraction.metadata_helper import build_doc_metadata_map
from src.modules.digester.extractors.rest.endpoints import extract_endpoints as _extract_rest_endpoints
from src.modules.digester.extractors.scim.endpoints import pregenerate_scim_endpoints
from src.modules.digester.extractors.sql.tables import extract_sql_tables
from src.modules.digester.persistence import persist_object_class_field
from src.modules.digester.selection import (
    DEFAULT_CRITERIA,
    build_chunk_id_to_doc_id,
    build_relevant_chunks_from_doc_items,
    chunk_ids_from_relevant_chunks,
    exclude_doc_items_by_chunk_id,
    select_doc_chunks,
)

logger = logging.getLogger(__name__)


def _endpoint_result_has_items(extraction_result: Dict[str, Any]) -> bool:
    return len(extract_endpoints_from_result(extraction_result)) > 0


async def _extract_rest_endpoints_from_relevant_chunks(
    doc_items: List[dict],
    object_class: str,
    relevant_chunks: List[Dict[str, Any]],
    job_id: UUID,
    base_api_url: str,
    *,
    exclude_conndev: bool = False,
) -> Dict[str, Any] | None:
    if exclude_conndev:
        doc_items = _exclude_conndev_documents(doc_items)

    selected_content, chunk_ids = select_doc_chunks(doc_items, relevant_chunks, "Digester:Endpoints")

    if not selected_content:
        return None

    chunk_metadata_map = build_doc_metadata_map(doc_items)
    chunk_id_to_doc_id = build_chunk_id_to_doc_id(doc_items)

    total_chunks = len(selected_content)
    logger.info(
        "[Digester:Endpoints] Processing %d pre-selected chunks for %s (chunk IDs: %s)",
        total_chunks,
        object_class,
        chunk_ids,
    )

    return await _extract_rest_endpoints(
        selected_content,
        object_class,
        job_id,
        base_api_url,
        chunk_ids,
        chunk_metadata_map,
        chunk_id_to_doc_id,
    )


async def _retry_rest_endpoints_with_default_criteria(
    primary_result: Dict[str, Any],
    object_class: str,
    session_id: UUID,
    relevant_chunks: List[Dict[str, Any]],
    job_id: UUID,
    base_api_url: str,
    *,
    exclude_conndev: bool = False,
) -> Dict[str, Any]:
    if _endpoint_result_has_items(primary_result):
        return primary_result

    logger.info(
        "[Digester:Endpoints] Endpoint-focused chunks produced empty final endpoints for session %s, object class %s; "
        "retrying with DEFAULT_CRITERIA",
        session_id,
        object_class,
    )
    await update_job_progress(
        job_id,
        stage="chunking",
        message=f"No endpoints found in endpoint-focused chunks for {object_class}; retrying with broader filter",
    )

    fallback_doc_items = await filter_documentation_items(DEFAULT_CRITERIA, session_id)
    if exclude_conndev:
        fallback_doc_items = _exclude_conndev_documents(fallback_doc_items)
    if not fallback_doc_items:
        logger.info(
            "[Digester:Endpoints] DEFAULT_CRITERIA matched no documentation for session %s; keeping empty endpoint result",
            session_id,
        )
        return primary_result

    primary_chunk_ids = chunk_ids_from_relevant_chunks(relevant_chunks)
    fallback_relevant_chunks = build_relevant_chunks_from_doc_items(fallback_doc_items)
    fallback_chunk_ids = chunk_ids_from_relevant_chunks(fallback_relevant_chunks)
    if primary_chunk_ids and primary_chunk_ids == fallback_chunk_ids:
        logger.info(
            "[Digester:Endpoints] DEFAULT_CRITERIA matched same chunks for session %s, object class %s; skipping retry",
            session_id,
            object_class,
        )
        return primary_result

    fallback_doc_items = exclude_doc_items_by_chunk_id(fallback_doc_items, primary_chunk_ids)
    fallback_relevant_chunks = build_relevant_chunks_from_doc_items(fallback_doc_items)
    if not fallback_relevant_chunks:
        logger.info(
            "[Digester:Endpoints] DEFAULT_CRITERIA produced no new chunks for session %s; keeping empty endpoint result",
            session_id,
        )
        return primary_result

    fallback_result = await _extract_rest_endpoints_from_relevant_chunks(
        fallback_doc_items,
        object_class,
        fallback_relevant_chunks,
        job_id,
        base_api_url,
        exclude_conndev=exclude_conndev,
    )
    if fallback_result is None:
        logger.info(
            "[Digester:Endpoints] DEFAULT_CRITERIA chunks could not be selected for session %s; keeping empty endpoint result",
            session_id,
        )
        return primary_result

    return fallback_result


def _exclude_conndev_documents(doc_items: List[dict]) -> List[dict]:
    """Keep connector-export contracts out of documentation-driven endpoint extraction."""
    return [item for item in doc_items if not is_conndev_documentation_item(item)]


async def extract_endpoints(
    doc_items: List[dict],
    object_class: str,
    session_id: UUID,
    relevant_chunks: List[Dict[str, Any]],
    job_id: UUID,
    base_api_url: str = "",
    api_type_override: ApiType | None = None,
    object_class_flags: Mapping[str, Any] | None = None,
):
    """
    Extract endpoints from only the relevant chunks of documentation and update the specific object class
    in objectClassesOutput with the extracted endpoints.

    The extraction protocol (REST/SCIM/SQL) is taken from ``api_type_override`` when provided,
    otherwise it is derived from the apiType stored in the session ``infoMetadata``.

    Args:
        doc_items: Full documentation items
        object_class: Name of the object class
        session_id: Session ID
        relevant_chunks: List of {doc_id, chunk_id} dicts indicating which chunks to process
        job_id: Job ID for progress tracking
        base_api_url: Base API URL for endpoint extraction
        api_type_override: Explicit protocol override; falls back to detected apiType when None
        object_class_flags: Structural flags loaded during request orchestration for SCIM endpoint routing
    """

    protocol = await resolve_effective_api_type(session_id, api_type_override)
    if protocol == ApiType.SQL:
        result = await extract_sql_tables(doc_items, object_class, job_id)
        tables_list = extract_endpoints_from_result(result)
        logger.info("[Digester:Endpoints] Selected %d SQL tables for %s", len(tables_list), object_class)
        await persist_object_class_field(session_id, object_class, "endpoints", tables_list, "Digester:Endpoints")
        return result

    is_scim = protocol == ApiType.SCIM

    if is_scim:
        deterministic_result = await pregenerate_scim_endpoints(
            session_id=session_id,
            object_class=object_class,
            job_id=job_id,
            object_class_flags=object_class_flags,
        )
        if deterministic_result is not None:
            result = deterministic_result
        else:
            documented_result = await _extract_rest_endpoints_from_relevant_chunks(
                doc_items,
                object_class,
                relevant_chunks,
                job_id,
                base_api_url,
                exclude_conndev=True,
            )
            if documented_result is None:
                documented_result = build_endpoint_result()

            result = await _retry_rest_endpoints_with_default_criteria(
                documented_result,
                object_class,
                session_id,
                relevant_chunks,
                job_id,
                base_api_url,
                exclude_conndev=True,
            )
    else:
        rest_result = await _extract_rest_endpoints_from_relevant_chunks(
            doc_items,
            object_class,
            relevant_chunks,
            job_id,
            base_api_url,
        )
        if rest_result is None:
            if not relevant_chunks:
                logger.warning(f"[Digester:Endpoints] No relevant chunks found for {object_class}")
                return build_endpoint_result()
            rest_result = build_endpoint_result()

        result = await _retry_rest_endpoints_with_default_criteria(
            rest_result,
            object_class,
            session_id,
            relevant_chunks,
            job_id,
            base_api_url,
        )

    endpoints_list = extract_endpoints_from_result(result)
    logger.info("[Digester:Endpoints] Extracted %d endpoints for %s", len(endpoints_list), object_class)
    await persist_object_class_field(session_id, object_class, "endpoints", endpoints_list, "Digester:Endpoints")

    return result
