# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
SCIM 2.0 endpoint pregeneration.

Endpoints for a SCIM object class are produced deterministically from the object-class /
resource mapping and the SCIM baseline schemas — no LLM call is involved.
"""

import logging
from typing import Any, Dict, List
from uuid import UUID

from src.common.database.config import async_session_maker
from src.common.database.repositories.session_repository import SessionRepository
from src.common.jobs import increment_processed_documents, update_job_progress
from src.common.utils.normalize import normalize_chunk_pair
from src.modules.digester.entities.scim_resource import extract_scim_resource_path, infer_scim_resource_path
from src.modules.digester.extractors.scim.baseline import (
    generate_scim_crud_endpoints,
    get_scim_canonical_class_name,
    is_scim_extension_schema,
    load_session_scim_schemas,
)

logger = logging.getLogger(__name__)


async def pregenerate_scim_endpoints(
    *,
    session_id: UUID,
    object_class: str,
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

    scim_schemas = await load_session_scim_schemas(session_id)

    endpoints: List[Dict[str, Any]]
    if is_scim_extension_schema(scim_schemas, object_class):
        logger.info("[SCIM:Endpoints] %s is a SCIM extension schema; skipping standalone endpoints", object_class)
        endpoints = []
    else:
        # object_class arrives lower-cased for case-insensitive matching; prefer the schema's canonical
        # name so schema-backed resources keep their proper casing (User -> /Users, not /users).
        canonical_class = get_scim_canonical_class_name(scim_schemas, object_class) or object_class
        resource_path = extract_scim_resource_path(object_class_data) or infer_scim_resource_path(canonical_class)
        endpoints = generate_scim_crud_endpoints(resource_path, canonical_class)

    await increment_processed_documents(job_id, delta=1)

    logger.info(
        "[SCIM:Endpoints] Pregenerated %d endpoints for %s",
        len(endpoints),
        object_class,
    )

    normalized_pairs = [normalize_chunk_pair(chunk_ref) for chunk_ref in relevant_chunks]
    valid_pairs = sorted({pair for pair in normalized_pairs if pair is not None}, key=lambda pair: (pair[0], pair[1]))
    endpoint_relevant = [{"docId": doc_id, "chunkId": chunk_id} for doc_id, chunk_id in valid_pairs]
    endpoints_with_references = [dict(endpoint, relevantDocumentations=endpoint_relevant) for endpoint in endpoints]

    return {
        "result": {"endpoints": endpoints_with_references},
        "relevantDocumentations": relevant_chunks,
    }
