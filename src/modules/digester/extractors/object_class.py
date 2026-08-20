# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Object-class extraction workflow.

Owns the entity-level flow: resolve the effective protocol and dispatch to the
REST/SCIM/SQL leaf extractors, then (for REST) fan out per-chunk extraction,
merge/deduplicate and sort. Protocol-specific leaves live under
``extractors/rest``, ``extractors/scim`` and ``extractors/sql``.
"""

import logging
from typing import Any, Dict, List
from uuid import UUID

from src.documents.normalize import canonical_object_class_key
from src.modules.digester.aggregation.object_class_ranking import deduplicate_and_sort_object_classes
from src.modules.digester.extraction.chunk_extraction import run_doc_extractors_concurrently
from src.modules.digester.extraction.metadata_helper import build_doc_metadata_map
from src.modules.digester.extractors.rest.object_class import (
    build_object_class_extraction_chain,
    extract_object_classes_raw,
)
from src.modules.digester.extractors.scim.object_class import extract_scim_object_classes
from src.modules.digester.extractors.sql.object_class import extract_sql_object_classes
from src.modules.digester.selection import build_chunk_id_to_doc_id
from src.session.info_metadata import resolve_effective_api_type
from src.shared.enums import ApiType, GenerationIntent

logger = logging.getLogger(__name__)


async def extract_object_classes(
    doc_items: List[dict],
    job_id: UUID,
    session_id: UUID,
    api_type_override: ApiType | None = None,
    intent: GenerationIntent = GenerationIntent.MANAGEMENT,
):
    """
    Extract object classes from multiple documentation items and return merged result with metadata.

    The extraction protocol (REST/SCIM/SQL) is taken from ``api_type_override`` when provided,
    otherwise it is derived from the apiType stored in the session ``infoMetadata``.

    Args:
        doc_items: List of documentation items to process
        job_id: Job ID for progress tracking
        session_id: Session ID to retrieve api_type from infoMetadata
        api_type_override: Explicit protocol override; falls back to detected apiType when None
        intent: Business-domain lens for object-class prioritization
            (management/itsm/management_itsm)

    Returns:
        Dictionary with result and relevantDocumentations
    """
    protocol = await resolve_effective_api_type(session_id, api_type_override)
    if protocol == ApiType.SQL:
        return await extract_sql_object_classes(doc_items, job_id, intent=intent)

    if protocol == ApiType.SCIM:
        return await extract_scim_object_classes(doc_items, job_id, session_id, intent=intent)

    return await _extract_rest_object_classes(doc_items, job_id, intent=intent)


async def _extract_rest_object_classes(
    doc_items: List[dict],
    job_id: UUID,
    intent: GenerationIntent = GenerationIntent.MANAGEMENT,
):
    """
    REST-specific object class extraction.

    Step 1: Extract raw object classes from each chunk (by chunkId) - processes chunks in parallel
    Step 2: Merge/deduplicate classes
    Step 3: Enrich with confidence and sort final output
    """
    all_object_classes = []
    all_relevant_chunks: List[Dict[str, Any]] = []
    class_to_chunks: Dict[str, List[Dict[str, Any]]] = {}
    chunk_id_to_doc_id = build_chunk_id_to_doc_id(doc_items)

    chunk_metadata_map = build_doc_metadata_map(doc_items)
    extraction_chain = build_object_class_extraction_chain(intent) if doc_items else None

    async def extractor_with_metadata(content: str, job_id: UUID, chunk_id: UUID):
        chunk_metadata = chunk_metadata_map.get(str(chunk_id))
        return await extract_object_classes_raw(
            content,
            job_id,
            chunk_id,
            chunk_metadata,
            extraction_chain=extraction_chain,
            intent=intent,
        )

    # Process all chunks in parallel using the generic function
    results = await run_doc_extractors_concurrently(
        chunk_items=doc_items,
        job_id=job_id,
        extractor=extractor_with_metadata,
    )

    # Collect results from all chunks
    for raw_classes, has_relevant_data, chunk_uuid in results:
        chunk_id = str(chunk_uuid)
        doc_id = chunk_id_to_doc_id.get(chunk_id)

        logger.info(
            "[Digester:ObjectClasses] Chunk %s: extracted %s object classes",
            chunk_id,
            len(raw_classes),
        )
        # For each object class, track which document chunks it appears in
        # Only add chunks that are specifically relevant to this object class
        for obj_class in raw_classes:
            class_name = canonical_object_class_key(obj_class.name)
            if class_name not in class_to_chunks:
                class_to_chunks[class_name] = []

            if doc_id:
                class_to_chunks[class_name].append({"doc_id": doc_id, "chunk_id": chunk_id})
            else:
                logger.warning(
                    "[Digester:ObjectClasses] Missing docId for chunk %s, skipping relevant chunk mapping for class %s",
                    chunk_id,
                    obj_class.name,
                )

        all_object_classes.extend(raw_classes)
        if has_relevant_data and doc_id:
            all_relevant_chunks.append({"doc_id": doc_id, "chunk_id": chunk_id})
        elif has_relevant_data:
            logger.warning(
                "[Digester:ObjectClasses] Missing docId for chunk %s, skipping top-level relevant chunk mapping",
                chunk_id,
            )

    logger.info(
        "[Digester:ObjectClasses] Processing complete. Total: %s object classes from %s chunks. "
        "Starting deduplication and sorting...",
        len(all_object_classes),
        len(doc_items),
    )
    final_result = await deduplicate_and_sort_object_classes(
        all_object_classes,
        job_id,
        class_to_chunks,
        intent=intent,
    )

    return {
        "result": final_result.model_dump(by_alias=True),
        "relevantDocumentations": all_relevant_chunks,
    }
