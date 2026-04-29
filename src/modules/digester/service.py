# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Any, Dict, List, cast
from uuid import UUID

from src.common.chunk_filter.filter import filter_documentation_items
from src.common.jobs import update_job_progress
from src.common.utils.session_info_metadata import get_session_api_types, is_scim_api

# Shared extractors
from src.modules.digester.extractors.auth import deduplicate_and_sort_auth, extract_auth_raw
from src.modules.digester.extractors.info import extract_info_metadata as _extract_info_metadata

# REST extractors
from src.modules.digester.extractors.rest.attributes import extract_attributes as _extract_rest_attributes
from src.modules.digester.extractors.rest.endpoints import extract_endpoints as _extract_rest_endpoints
from src.modules.digester.extractors.rest.object_class import (
    build_object_class_extraction_chain,
    deduplicate_and_sort_object_classes,
    extract_object_classes_raw,
)
from src.modules.digester.extractors.rest.relations import (
    extract_relations as _extract_relations,
)
from src.modules.digester.extractors.rest.relations import (
    sort_relation_dicts_by_iga_priority,
)

# SCIM extractors
from src.modules.digester.extractors.scim.attributes import extract_scim_attributes
from src.modules.digester.extractors.scim.endpoints import pregenerate_scim_endpoints
from src.modules.digester.extractors.scim.object_class import extract_scim_object_classes
from src.modules.digester.schema import InfoMetadata, InfoResponse
from src.modules.digester.utils.chunk_extraction import process_over_chunks, run_doc_extractors_concurrently
from src.modules.digester.utils.criteria import DEFAULT_CRITERIA
from src.modules.digester.utils.doc_chunk import (
    build_chunk_id_to_doc_id,
    build_relevant_chunks_from_doc_items,
    chunk_ids_from_relevant_chunks,
    exclude_doc_items_by_chunk_id,
    select_doc_chunks,
)
from src.modules.digester.utils.merges import (
    merge_info_metadata,
    merge_relations_results,
)
from src.modules.digester.utils.metadata_helper import build_doc_metadata_map
from src.modules.digester.utils.object_classes import (
    extract_attributes_from_result,
    extract_endpoints_from_result,
    update_object_class_field_in_session,
)

logger = logging.getLogger(__name__)


async def extract_object_classes(
    doc_items: List[dict],
    job_id: UUID,
    session_id: UUID,
):
    """
    Extract object classes from multiple documentation items and return merged result with metadata.

    This function automatically detects whether to use REST or SCIM extraction based on the
    api_type from the infoMetadata stored in the session.

    Args:
        doc_items: List of documentation items to process
        job_id: Job ID for progress tracking
        session_id: Session ID to retrieve api_type from infoMetadata

    Returns:
        Dictionary with result and relevantDocumentations
    """
    api_type = await get_session_api_types(session_id)
    is_scim = is_scim_api(api_type)

    if is_scim:
        return await extract_scim_object_classes(doc_items, job_id)

    return await _extract_rest_object_classes(doc_items, job_id)


async def _extract_rest_object_classes(
    doc_items: List[dict],
    job_id: UUID,
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
    extraction_chain = build_object_class_extraction_chain() if doc_items else None

    async def extractor_with_metadata(content: str, job_id: UUID, chunk_id: UUID):
        chunk_metadata = chunk_metadata_map.get(str(chunk_id))
        return await extract_object_classes_raw(
            content,
            job_id,
            chunk_id,
            chunk_metadata,
            extraction_chain=extraction_chain,
        )

    # Process all chunks in parallel using the generic function
    results = await run_doc_extractors_concurrently(
        chunk_items=doc_items,
        job_id=job_id,
        extractor=extractor_with_metadata,
        logger_scope="Digester:ObjectClasses",
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
            class_name = obj_class.name.strip().lower()
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
    )

    return {
        "result": final_result.model_dump(by_alias=True) if hasattr(final_result, "model_dump") else final_result,
        "relevantDocumentations": all_relevant_chunks,
    }


async def extract_auth(doc_items: List[dict], job_id: UUID):
    """
    Extract authentication info from multiple documentation items and return merged result with metadata.

    Step 1: Extract raw auth info from each chunk (by chunkId) - processes chunks in parallel
    Step 2: Merge, deduplicate and sort ALL auth info together
    """
    all_auth_info = []
    all_relevant_chunks: List[Dict[str, Any]] = []
    chunk_id_to_doc_id = build_chunk_id_to_doc_id(doc_items)
    chunk_metadata_map = build_doc_metadata_map(doc_items)

    async def extractor_with_metadata(content: str, job_id: UUID, chunk_id: UUID):
        chunk_metadata = chunk_metadata_map.get(str(chunk_id))
        return await extract_auth_raw(content, job_id, chunk_id, chunk_metadata)

    # Process all chunks in parallel using the generic function
    results = await run_doc_extractors_concurrently(
        chunk_items=doc_items,
        job_id=job_id,
        extractor=extractor_with_metadata,
        logger_scope="Digester:Auth",
    )

    # Collect results from all chunks
    for raw_auth, has_relevant_data, chunk_id in results:
        logger.info(
            "[Digester:Auth] Chunk %s: extracted %s auth items",
            chunk_id,
            len(raw_auth),
        )
        all_auth_info.extend(raw_auth)
        if has_relevant_data:
            chunk_id_str = str(chunk_id)
            doc_id = chunk_id_to_doc_id.get(chunk_id_str)
            if doc_id:
                all_relevant_chunks.append({"doc_id": doc_id, "chunk_id": chunk_id_str})
            else:
                logger.warning(
                    "[Digester:Auth] Missing docId for chunk %s, skipping relevant chunk mapping",
                    chunk_id_str,
                )

    logger.info(
        "[Digester:Auth] Processing complete. Total: %s auth items from %s chunks. "
        "Starting deduplication and sorting...",
        len(all_auth_info),
        len(doc_items),
    )
    final_result = await deduplicate_and_sort_auth(all_auth_info, job_id)

    return {
        "result": final_result.model_dump(by_alias=True) if hasattr(final_result, "model_dump") else final_result,
        "relevantDocumentations": all_relevant_chunks,
    }


def _auth_result_has_items(extraction_result: Dict[str, Any]) -> bool:
    result = extraction_result.get("result")
    if not isinstance(result, dict):
        return False
    auth = result.get("auth")
    return isinstance(auth, list) and len(auth) > 0


def _endpoint_result_has_items(extraction_result: Dict[str, Any]) -> bool:
    return len(extract_endpoints_from_result(extraction_result)) > 0


def _attribute_result_has_items(extraction_result: Dict[str, Any]) -> bool:
    return len(extract_attributes_from_result(extraction_result)) > 0


async def extract_auth_with_fallback(
    doc_items: List[dict],
    used_auth_criteria: bool,
    session_id: UUID,
    job_id: UUID,
):
    """
    Run auth extraction on AUTH_CRITERIA results first.
    If empty and AUTH_CRITERIA was used, retry once using DEFAULT_CRITERIA docs.
    """
    primary_result = await extract_auth(doc_items, job_id)
    if not used_auth_criteria or _auth_result_has_items(primary_result):
        return primary_result

    logger.info(
        "[Digester:Auth] AUTH_CRITERIA produced empty final auth result for session %s; retrying with DEFAULT_CRITERIA",
        session_id,
    )
    await update_job_progress(
        job_id,
        stage="chunking",
        message="No auth found in auth-focused chunks; retrying with broader documentation filter",
    )

    fallback_doc_items = await filter_documentation_items(DEFAULT_CRITERIA, session_id)
    if not fallback_doc_items:
        logger.info(
            "[Digester:Auth] DEFAULT_CRITERIA matched no documentation for session %s; keeping empty auth result",
            session_id,
        )
        return primary_result

    primary_chunk_ids = {str(item.get("chunkId")) for item in doc_items if item.get("chunkId")}
    fallback_chunk_ids = {str(item.get("chunkId")) for item in fallback_doc_items if item.get("chunkId")}
    if primary_chunk_ids and primary_chunk_ids == fallback_chunk_ids:
        logger.info(
            "[Digester:Auth] DEFAULT_CRITERIA matched same chunks as AUTH_CRITERIA for session %s; skipping retry",
            session_id,
        )
        return primary_result

    return await extract_auth(fallback_doc_items, job_id)


async def _extract_rest_attributes_from_relevant_chunks(
    doc_items: List[dict],
    object_class: str,
    relevant_chunks: List[Dict[str, Any]],
    job_id: UUID,
) -> Dict[str, Any] | None:
    selected_content, chunk_ids = select_doc_chunks(doc_items, relevant_chunks, "Digester:Attributes")

    if not selected_content:
        return None

    chunk_metadata_map = build_doc_metadata_map(doc_items)
    chunk_id_to_doc_id = build_chunk_id_to_doc_id(doc_items)

    total_chunks = len(selected_content)
    logger.info(
        "[Digester:Attributes] Processing %d pre-selected chunks for %s (chunk IDs: %s)",
        total_chunks,
        object_class,
        chunk_ids,
    )

    return await _extract_rest_attributes(
        selected_content,
        object_class,
        job_id,
        chunk_ids,
        chunk_metadata_map,
        chunk_id_to_doc_id,
    )


async def _retry_rest_attributes_with_default_criteria(
    primary_result: Dict[str, Any],
    object_class: str,
    session_id: UUID,
    relevant_chunks: List[Dict[str, Any]],
    job_id: UUID,
) -> Dict[str, Any]:
    if _attribute_result_has_items(primary_result):
        return primary_result

    logger.info(
        "[Digester:Attributes] Object-class chunks produced empty final attributes for session %s, object class %s; "
        "retrying with DEFAULT_CRITERIA",
        session_id,
        object_class,
    )
    await update_job_progress(
        job_id,
        stage="chunking",
        message=f"No attributes found in object-class chunks for {object_class}; retrying with broader filter",
    )

    fallback_doc_items = await filter_documentation_items(DEFAULT_CRITERIA, session_id)
    if not fallback_doc_items:
        logger.info(
            "[Digester:Attributes] DEFAULT_CRITERIA matched no documentation for session %s; "
            "keeping empty attribute result",
            session_id,
        )
        return primary_result

    primary_chunk_ids = chunk_ids_from_relevant_chunks(relevant_chunks)
    fallback_relevant_chunks = build_relevant_chunks_from_doc_items(fallback_doc_items)
    fallback_chunk_ids = chunk_ids_from_relevant_chunks(fallback_relevant_chunks)
    if primary_chunk_ids and primary_chunk_ids == fallback_chunk_ids:
        logger.info(
            "[Digester:Attributes] DEFAULT_CRITERIA matched same chunks for session %s, object class %s; "
            "skipping retry",
            session_id,
            object_class,
        )
        return primary_result

    fallback_doc_items = exclude_doc_items_by_chunk_id(fallback_doc_items, primary_chunk_ids)
    fallback_relevant_chunks = build_relevant_chunks_from_doc_items(fallback_doc_items)
    if not fallback_relevant_chunks:
        logger.info(
            "[Digester:Attributes] DEFAULT_CRITERIA produced no new chunks for session %s; "
            "keeping empty attribute result",
            session_id,
        )
        return primary_result

    fallback_result = await _extract_rest_attributes_from_relevant_chunks(
        fallback_doc_items,
        object_class,
        fallback_relevant_chunks,
        job_id,
    )
    if fallback_result is None:
        logger.info(
            "[Digester:Attributes] DEFAULT_CRITERIA chunks could not be selected for session %s; "
            "keeping empty attribute result",
            session_id,
        )
        return primary_result

    return fallback_result


async def _extract_rest_endpoints_from_relevant_chunks(
    doc_items: List[dict],
    object_class: str,
    relevant_chunks: List[Dict[str, Any]],
    job_id: UUID,
    base_api_url: str,
) -> Dict[str, Any] | None:
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
    )
    if fallback_result is None:
        logger.info(
            "[Digester:Endpoints] DEFAULT_CRITERIA chunks could not be selected for session %s; keeping empty endpoint result",
            session_id,
        )
        return primary_result

    return fallback_result


async def extract_info_metadata(doc_items: List[dict], job_id: UUID):
    """
    Extract metadata from multiple documentation items in parallel.

    Step 1: Extract raw InfoMetadata candidates from each chunk (by chunkId) in parallel.
    Step 2: Merge all candidates using threshold-based heuristics into one final InfoResponse payload.
    """
    all_info_candidates: List[InfoMetadata] = []
    all_relevant_chunks: List[Dict[str, Any]] = []
    chunk_id_to_doc_id = build_chunk_id_to_doc_id(doc_items)
    chunk_metadata_map = build_doc_metadata_map(doc_items)

    async def extractor_with_metadata(content: str, job_id: UUID, chunk_id: UUID):
        chunk_metadata = chunk_metadata_map.get(str(chunk_id))
        return await _extract_info_metadata(content, job_id, chunk_id, chunk_metadata)

    results = await run_doc_extractors_concurrently(
        chunk_items=doc_items,
        job_id=job_id,
        extractor=extractor_with_metadata,
        logger_scope="Digester:InfoMetadata",
    )

    for raw_infos, has_relevant_data, chunk_id in results:
        normalized_infos: List[InfoMetadata] = []

        if isinstance(raw_infos, list):
            for item in raw_infos:
                if isinstance(item, InfoMetadata):
                    normalized_infos.append(item)
                    continue
                if isinstance(item, InfoResponse):
                    if item.info_metadata is not None:
                        normalized_infos.append(item.info_metadata)
                    continue
                if isinstance(item, dict):
                    try:
                        parsed_info = InfoResponse.model_validate(item).info_metadata
                        if parsed_info is not None:
                            normalized_infos.append(parsed_info)
                        continue
                    except Exception:
                        try:
                            normalized_infos.append(InfoMetadata.model_validate(item))
                        except Exception:
                            continue
        elif isinstance(raw_infos, InfoMetadata):
            normalized_infos.append(raw_infos)
        elif isinstance(raw_infos, InfoResponse):
            if raw_infos.info_metadata is not None:
                normalized_infos.append(raw_infos.info_metadata)
        elif isinstance(raw_infos, dict):
            try:
                parsed_info = InfoResponse.model_validate(raw_infos).info_metadata
                if parsed_info is not None:
                    normalized_infos.append(parsed_info)
            except Exception:
                try:
                    normalized_infos.append(InfoMetadata.model_validate(raw_infos))
                except Exception:
                    pass

        logger.info(
            "[Digester:InfoMetadata] Chunk %s: extracted %s metadata candidates",
            chunk_id,
            len(normalized_infos),
        )
        all_info_candidates.extend(normalized_infos)

        if has_relevant_data:
            chunk_id_str = str(chunk_id)
            doc_id = chunk_id_to_doc_id.get(chunk_id_str)
            if doc_id:
                all_relevant_chunks.append({"doc_id": doc_id, "chunk_id": chunk_id_str})
            else:
                logger.warning(
                    "[Digester:InfoMetadata] Missing docId for chunk %s, skipping relevant chunk mapping",
                    chunk_id_str,
                )

    logger.info(
        "[Digester:InfoMetadata] Processing complete. Total: %s candidates from %s chunks. Starting heuristic merge...",
        len(all_info_candidates),
        len(doc_items),
    )

    merged_result = merge_info_metadata(all_info_candidates, total_items=len(doc_items))
    await update_job_progress(job_id, stage="aggregation_finished", message="Extraction complete; finalizing")

    return {
        "result": merged_result,
        "relevantDocumentations": all_relevant_chunks,
    }


async def extract_attributes(
    doc_items: List[dict],
    object_class: str,
    session_id: UUID,
    relevant_chunks: List[Dict[str, Any]],
    job_id: UUID,
):
    """
    Extract attributes from only the relevant chunks of documentation and update the specific object class
    in objectClassesOutput with the extracted attributes.

    This function automatically detects whether to use REST or SCIM extraction based on the
    api_type from the infoMetadata stored in the session.

    Args:
        doc_items: Full documentation items
        object_class: Name of the object class
        session_id: Session ID
        relevant_chunks: List of {doc_id, chunk_id} dicts indicating which chunks to process
        job_id: Job ID for progress tracking
    """
    if not relevant_chunks:
        logger.warning(f"[Digester:Attributes] No relevant chunks found for {object_class}")
        return {"result": {"attributes": {}}, "relevantDocumentations": []}

    api_type = await get_session_api_types(session_id)
    is_scim = is_scim_api(api_type)

    if is_scim:
        selected_content, chunk_ids = select_doc_chunks(doc_items, relevant_chunks, "Digester:Attributes")
        if not selected_content:
            logger.warning(f"[Digester:Attributes] No relevant chunks found for {object_class}")
            return {"result": {"attributes": {}}, "relevantDocumentations": []}

        chunk_metadata_map = build_doc_metadata_map(doc_items)
        chunk_id_to_doc_id = build_chunk_id_to_doc_id(doc_items)
        result = await extract_scim_attributes(
            selected_content,
            object_class,
            job_id,
            chunk_ids,
            chunk_metadata_map,
            chunk_id_to_doc_id,
        )
    else:
        rest_result = await _extract_rest_attributes_from_relevant_chunks(
            doc_items,
            object_class,
            relevant_chunks,
            job_id,
        )
        if rest_result is None:
            if not relevant_chunks:
                logger.warning(f"[Digester:Attributes] No relevant chunks found for {object_class}")
                return {"result": {"attributes": {}}, "relevantDocumentations": []}
            rest_result = {"result": {"attributes": {}}, "relevantDocumentations": []}

        result = await _retry_rest_attributes_with_default_criteria(
            rest_result,
            object_class,
            session_id,
            relevant_chunks,
            job_id,
        )

    try:
        attributes_dict = extract_attributes_from_result(result)
        logger.info("[Digester:Attributes] Extracted %d attributes for %s", len(attributes_dict), object_class)

        updated = await update_object_class_field_in_session(
            session_id=session_id,
            object_class=object_class,
            field_name="attributes",
            field_value=attributes_dict,
        )
        if not updated:
            logger.warning("[Digester:Attributes] Failed to update objectClassesOutput for %s", object_class)

    except Exception as e:
        logger.exception(f"[Digester:Attributes] Exception while updating object class with attributes: {e}")

    return result


async def extract_endpoints(
    doc_items: List[dict],
    object_class: str,
    session_id: UUID,
    relevant_chunks: List[Dict[str, Any]],
    job_id: UUID,
    base_api_url: str = "",
):
    """
    Extract endpoints from only the relevant chunks of documentation and update the specific object class
    in objectClassesOutput with the extracted endpoints.

    This function automatically detects whether to use REST or SCIM extraction based on the
    api_type from the infoMetadata stored in the session.

    Args:
        doc_items: Full documentation items
        object_class: Name of the object class
        session_id: Session ID
        relevant_chunks: List of {doc_id, chunk_id} dicts indicating which chunks to process
        job_id: Job ID for progress tracking
        base_api_url: Base API URL for endpoint extraction
    """

    api_type = await get_session_api_types(session_id)
    is_scim = is_scim_api(api_type)

    if is_scim:
        result = await pregenerate_scim_endpoints(
            session_id=session_id,
            object_class=object_class,
            base_api_url=base_api_url,
            job_id=job_id,
            relevant_chunks=relevant_chunks,
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
                return {"result": {"endpoints": []}, "relevantDocumentations": []}
            rest_result = {"result": {"endpoints": []}, "relevantDocumentations": []}

        result = await _retry_rest_endpoints_with_default_criteria(
            rest_result,
            object_class,
            session_id,
            relevant_chunks,
            job_id,
            base_api_url,
        )

    try:
        endpoints_list = extract_endpoints_from_result(result)
        logger.info("[Digester:Endpoints] Extracted %d endpoints for %s", len(endpoints_list), object_class)

        updated = await update_object_class_field_in_session(
            session_id=session_id,
            object_class=object_class,
            field_name="endpoints",
            field_value=endpoints_list,
        )
        if not updated:
            logger.warning("[Digester:Endpoints] Failed to update objectClassesOutput for %s", object_class)
    except Exception as e:
        logger.warning(f"[Digester:Endpoints] Failed to update object class with endpoints: {e}")

    return result


async def extract_relations(doc_items: List[dict], relevant_object_class: Any, job_id: UUID):
    """Extract relations from multiple documentation items."""

    chunk_metadata_map = build_doc_metadata_map(doc_items)

    def extractor(content: str, jid: UUID, chunk_id: UUID):
        chunk_metadata = chunk_metadata_map.get(str(chunk_id))
        return _extract_relations(content, relevant_object_class, jid, chunk_id, chunk_metadata)

    def per_chunk_count(d: Dict[str, Any]) -> int:
        return len(cast(List[dict], d.get("relations", [])))

    def merge_and_sort_relations(results: List[Dict[str, Any]]) -> Dict[str, Any]:
        merged = merge_relations_results(results)
        raw_relations = merged.get("relations", [])
        if not isinstance(raw_relations, list):
            merged["relations"] = []
            return merged

        merged["relations"] = sort_relation_dicts_by_iga_priority(raw_relations, relevant_object_class)
        return merged

    return await process_over_chunks(
        chunk_items=doc_items,
        job_id=job_id,
        extractor=extractor,
        merger=merge_and_sort_relations,
        logger_scope="Digester:Relations",
        per_chunk_count=per_chunk_count,
    )
