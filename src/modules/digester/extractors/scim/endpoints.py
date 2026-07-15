# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
SCIM 2.0 endpoint pregeneration.

Endpoints for a SCIM object class are produced deterministically only when the conndev
baseline explicitly exposes a resource path. Embedded, abstract and extension classes
are terminal non-resources. Other classes return control to the documentation extractor.
"""

import logging
from typing import Any, Dict
from uuid import UUID

from src.common.database.config import async_session_maker
from src.common.database.repositories.session_repository import SessionRepository
from src.common.jobs import increment_processed_documents, update_job_progress
from src.modules.digester.entities.object_classes import build_endpoint_result
from src.modules.digester.extractors.scim.baseline import (
    generate_scim_crud_endpoints,
    get_scim_canonical_class_name,
    get_scim_resource_endpoint_definition,
    is_scim_extension_schema,
    load_session_scim_baseline,
)

logger = logging.getLogger(__name__)


async def pregenerate_scim_endpoints(
    *,
    session_id: UUID,
    object_class: str,
    job_id: UUID,
) -> Dict[str, Any] | None:
    """
    Resolve a terminal deterministic SCIM endpoint result.

    Returns an endpoint result when the class is a non-resource or when conndev provides
    an explicit endpoint. Returns ``None`` when scraped documentation must be inspected.
    """
    await update_job_progress(
        job_id,
        total_processing=1,
        processing_completed=0,
        message=f"Pregenerating SCIM endpoints for {object_class}",
    )

    object_class_data: Dict[str, Any] = {}
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

    baseline_bundle = await load_session_scim_baseline(session_id)

    if _is_true(object_class_data.get("embedded")) or _is_true(object_class_data.get("abstract")):
        logger.info("[SCIM:Endpoints] %s is embedded or abstract; skipping standalone endpoints", object_class)
        await increment_processed_documents(job_id, delta=1)
        return build_endpoint_result()

    if is_scim_extension_schema(baseline_bundle, object_class):
        logger.info("[SCIM:Endpoints] %s is a SCIM extension schema; skipping standalone endpoints", object_class)
        await increment_processed_documents(job_id, delta=1)
        return build_endpoint_result()

    endpoint_definition = get_scim_resource_endpoint_definition(baseline_bundle, object_class)
    if endpoint_definition is None:
        logger.info(
            "[SCIM:Endpoints] No explicit conndev endpoint for %s; falling back to scraped documentation",
            object_class,
        )
        return None

    # object_class arrives lower-cased for case-insensitive matching; prefer the schema's canonical
    # name so schema-backed resources keep their proper casing (User -> /Users, not /users).
    canonical_class = get_scim_canonical_class_name(baseline_bundle.schemas, object_class) or object_class
    endpoints = generate_scim_crud_endpoints(endpoint_definition.endpoint, canonical_class)

    await increment_processed_documents(job_id, delta=1)

    logger.info(
        "[SCIM:Endpoints] Pregenerated %d endpoints for %s",
        len(endpoints),
        object_class,
    )

    source_reference = endpoint_definition.source_reference
    endpoint_relevant = [source_reference.to_api_dict()] if source_reference is not None else []
    endpoints_with_references = [dict(endpoint, relevantDocumentations=endpoint_relevant) for endpoint in endpoints]

    top_level_relevant = [source_reference.to_internal_dict()] if source_reference is not None else []
    return build_endpoint_result(endpoints_with_references, top_level_relevant)


def _is_true(value: Any) -> bool:
    """Accept persisted boolean flags and their legacy string representation."""
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")
