# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Endpoint extraction workflow.

Owns the entity-level flow: dispatch to the REST/SCIM leaf extractors, retry with the
broader fallback documentation when the endpoint-focused chunks yield no endpoints, and
persist the extracted endpoints back onto the object class. Protocol-specific leaves live
under ``extractors/rest`` and ``extractors/scim``.

Every attempt reads the documentation selection stored in the job input when the job was
scheduled; the worker never reloads the session's documentation.

SQL has no endpoints at all - a database connector reaches its data through the table
and column mapping declared by the native schema, and code generation reads that from
the extracted attributes. Endpoint extraction is therefore rejected for a SQL session
rather than silently producing an empty or REST-shaped result.
"""

import logging
from collections.abc import Mapping
from typing import Any, Dict, List
from uuid import UUID

from src.documents.selection import DocumentationSelection, SelectionRole
from src.jobs import update_job_progress
from src.modules.digester.entities.object_classes import build_endpoint_result, extract_endpoints_from_result
from src.modules.digester.errors import EndpointExtractionNotSupportedError
from src.modules.digester.extraction.metadata_helper import build_doc_metadata_map
from src.modules.digester.extractors.rest.endpoints import extract_endpoints as _extract_rest_endpoints
from src.modules.digester.extractors.scim.baseline import build_scim_baseline_from_documents
from src.modules.digester.extractors.scim.endpoints import pregenerate_scim_endpoints
from src.modules.digester.persistence import persist_object_class_field
from src.modules.digester.selection import build_chunk_id_to_doc_id, chunk_texts_and_ids
from src.shared.content_types import is_conndev_documentation_item
from src.shared.enums import ApiType

logger = logging.getLogger(__name__)


def _endpoint_result_has_items(extraction_result: Dict[str, Any]) -> bool:
    return len(extract_endpoints_from_result(extraction_result)) > 0


async def _extract_rest_endpoints_from_chunks(
    doc_items: List[dict],
    object_class: str,
    job_id: UUID,
    base_api_url: str,
) -> Dict[str, Any] | None:
    """Run the REST leaf over the given chunks; ``None`` when there is nothing to read."""
    if not doc_items:
        return None

    selected_content, chunk_ids = chunk_texts_and_ids(doc_items)

    logger.info(
        "[Digester:Endpoints] Processing %d pre-selected chunks for %s (chunk IDs: %s)",
        len(selected_content),
        object_class,
        chunk_ids,
    )

    return await _extract_rest_endpoints(
        selected_content,
        object_class,
        job_id,
        base_api_url,
        chunk_ids,
        build_doc_metadata_map(doc_items),
        build_chunk_id_to_doc_id(doc_items),
    )


async def _retry_rest_endpoints_with_fallback(
    primary_result: Dict[str, Any],
    selection: DocumentationSelection,
    object_class: str,
    job_id: UUID,
    base_api_url: str,
) -> Dict[str, Any]:
    """Retry over the stored fallback chunks (never a primary chunk) when the primary attempt found nothing."""
    if _endpoint_result_has_items(primary_result):
        return primary_result

    logger.info(
        "[Digester:Endpoints] Endpoint-focused chunks produced empty final endpoints for %s; "
        "retrying with fallback chunks",
        object_class,
    )
    await update_job_progress(
        job_id,
        stage="chunking",
        message=f"No endpoints found in endpoint-focused chunks for {object_class}; retrying with broader filter",
    )

    fallback_doc_items = selection.documentation_items(SelectionRole.FALLBACK)
    if not fallback_doc_items:
        logger.info(
            "[Digester:Endpoints] Fallback criteria produced no new chunks for %s; keeping empty endpoint result",
            object_class,
        )
        return primary_result

    fallback_result = await _extract_rest_endpoints_from_chunks(fallback_doc_items, object_class, job_id, base_api_url)
    if fallback_result is None:
        logger.info(
            "[Digester:Endpoints] Fallback chunks could not be selected for %s; keeping empty endpoint result",
            object_class,
        )
        return primary_result

    return fallback_result


def _exclude_conndev_documents(doc_items: List[dict]) -> List[dict]:
    """Keep connector-export contracts out of documentation-driven endpoint extraction."""
    return [item for item in doc_items if not is_conndev_documentation_item(item)]


async def extract_endpoints(
    selection: DocumentationSelection,
    object_class: str,
    session_id: UUID,
    job_id: UUID,
    protocol: ApiType,
    base_api_url: str = "",
    object_class_flags: Mapping[str, Any] | None = None,
):
    """
    Extract endpoints from the stored documentation selection and update the specific
    object class in objectClassesOutput with the extracted endpoints.

    Args:
        selection: Documentation captured when the job was scheduled (primary, fallback
            and, for SCIM, the conndev baseline chunks)
        object_class: Name of the object class
        session_id: Session ID
        job_id: Job ID for progress tracking
        protocol: Effective protocol resolved when the job was scheduled
        base_api_url: Base API URL for endpoint extraction
        object_class_flags: Structural flags loaded during request orchestration for SCIM endpoint routing
    """
    if protocol == ApiType.SQL:
        raise EndpointExtractionNotSupportedError(object_class, protocol.value)

    primary_doc_items = selection.documentation_items(SelectionRole.PRIMARY)

    if protocol == ApiType.SCIM:
        deterministic_result = await pregenerate_scim_endpoints(
            baseline_bundle=build_scim_baseline_from_documents(
                selection.documentation_items(SelectionRole.SCIM_BASELINE)
            ),
            object_class=object_class,
            job_id=job_id,
            object_class_flags=object_class_flags,
        )
        if deterministic_result is not None:
            result = deterministic_result
        else:
            documented_result = await _extract_rest_endpoints_from_chunks(
                _exclude_conndev_documents(primary_doc_items),
                object_class,
                job_id,
                base_api_url,
            )
            result = await _retry_rest_endpoints_with_fallback(
                documented_result if documented_result is not None else build_endpoint_result(),
                selection,
                object_class,
                job_id,
                base_api_url,
            )
    else:
        rest_result = await _extract_rest_endpoints_from_chunks(primary_doc_items, object_class, job_id, base_api_url)
        if rest_result is None:
            logger.warning("[Digester:Endpoints] No relevant chunks found for %s", object_class)
            return build_endpoint_result()

        result = await _retry_rest_endpoints_with_fallback(rest_result, selection, object_class, job_id, base_api_url)

    endpoints_list = extract_endpoints_from_result(result)
    logger.info("[Digester:Endpoints] Extracted %d endpoints for %s", len(endpoints_list), object_class)
    await persist_object_class_field(session_id, object_class, "endpoints", endpoints_list, "Digester:Endpoints")

    return result
