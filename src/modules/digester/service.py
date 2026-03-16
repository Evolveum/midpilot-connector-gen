# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Any, Callable, Dict, List, cast
from uuid import UUID

from ...common.jobs import increment_processed_documents, update_job_progress

# Shared extractors
from .extractors.auth import deduplicate_and_sort_auth, extract_auth_raw
from .extractors.info import extract_info_metadata as _extract_info_metadata

# REST extractors
from .extractors.rest.attributes import extract_attributes as _extract_rest_attributes
from .extractors.rest.endpoints import extract_endpoints as _extract_rest_endpoints
from .extractors.rest.object_class import deduplicate_and_sort_object_classes, extract_object_classes_raw
from .extractors.rest.relations import extract_relations as _extract_relations

# SCIM extractors
from .extractors.scim.attributes import extract_scim_attributes
from .extractors.scim.endpoints import pregenerate_scim_endpoints
from .extractors.scim.object_class import extract_scim_object_classes
from .utils.api_context import get_api_type_from_session, is_scim_api, protocol_selection_message
from .utils.doc_chunk import select_doc_chunks
from .utils.merges import (
    merge_relations_results,
)
from .utils.metadata_helper import build_doc_metadata_map
from .utils.object_classes import (
    extract_attributes_from_result,
    extract_endpoints_from_result,
    update_object_class_field_in_session,
)
from .utils.parallel_docs import process_documents_in_parallel

logger = logging.getLogger(__name__)


def _is_empty_info_result_payload(payload: Dict[str, Any]) -> bool:
    """Detect if InfoResponse-like payload has no extracted metadata."""
    info = (payload or {}).get("infoMetadata")
    if info is None:
        info = (payload or {}).get("InfoMetadata")
    if info is None:
        info = (payload or {}).get("infoAboutSchema")
    if info is None:
        return True

    return not bool(
        str(info.get("name") or "").strip()
        or str(info.get("applicationVersion") or "").strip()
        or str(info.get("apiVersion") or "").strip()
        or info.get("apiType")
        or info.get("baseApiEndpoint")
    )


async def _process_over_documents(
    *,
    doc_items: List[dict],
    job_id: UUID,
    extractor: Callable[[str, UUID, UUID], Any],
    merger: Callable[[List[Dict[str, Any]]], Dict[str, Any]],
    logger_scope: str,
    per_doc_count: Callable[[Dict[str, Any]], int] | None = None,
) -> Dict[str, Any]:
    """
    Generic pipeline to process docs in parallel, call extractor, collect results/chunks, merge, and return.
    """
    all_results: List[Dict[str, Any]] = []
    all_relevant_chunks: List[Dict[str, Any]] = []

    # Process all documents in parallel using the generic function
    results = await process_documents_in_parallel(
        doc_items=doc_items,
        job_id=job_id,
        extractor=extractor,
        logger_scope=logger_scope,
    )

    # Collect results from all documents
    for raw_result, has_relevant_data, doc_uuid in results:
        # Accept Pydantic models or dicts; normalize to dict
        if hasattr(raw_result, "model_dump"):
            result_data = cast(Dict[str, Any], raw_result.model_dump(by_alias=True))
        else:
            result_data = cast(Dict[str, Any], raw_result or {})

        if per_doc_count is not None:
            try:
                count = per_doc_count(result_data)
            except Exception:
                count = 0
            logger.info(f"[{logger_scope}] Document {doc_uuid}: extracted {count} items")

        if result_data:
            all_results.append(result_data)
        if has_relevant_data:
            all_relevant_chunks.append({"docUuid": str(doc_uuid)})

    merged_result: Dict[str, Any] = merger(all_results)

    return {
        "result": merged_result,
        "relevantChunks": all_relevant_chunks,
    }


async def extract_object_classes(
    doc_items: List[dict],
    filter_relevancy: bool,
    min_relevancy_level: str,
    job_id: UUID,
    session_id: UUID,
):
    """
    Extract object classes from multiple documentation items and return merged result with metadata.

    This function automatically detects whether to use REST or SCIM extraction based on the
    api_type from the infoMetadata stored in the session.

    Args:
        doc_items: List of documentation items to process
        filter_relevancy: Whether to filter by relevancy
        min_relevancy_level: Minimum relevancy level (low/medium/high)
        job_id: Job ID for progress tracking
        session_id: Session ID to retrieve api_type from infoMetadata

    Returns:
        Dictionary with result and relevantChunks
    """
    api_type = await get_api_type_from_session(session_id)
    is_scim = is_scim_api(api_type)

    logger.info(
        "%s",
        protocol_selection_message(
            "Digester:ObjectClasses",
            is_scim=is_scim,
            scim_mode="guided extraction",
            rest_mode="standard REST extraction",
        ),
    )

    if is_scim:
        return await extract_scim_object_classes(doc_items, job_id)

    return await _extract_rest_object_classes(doc_items, filter_relevancy, min_relevancy_level, job_id)


async def _extract_rest_object_classes(
    doc_items: List[dict],
    filter_relevancy: bool,
    min_relevancy_level: str,
    job_id: UUID,
):
    """
    REST-specific object class extraction (original implementation).

    Step 1: Extract raw object classes from each document (per UUID) - processes documents in parallel
    Step 2: Merge, deduplicate and sort ALL object classes together
    """
    all_object_classes = []
    all_relevant_chunks: List[Dict[str, Any]] = []
    class_to_chunks: Dict[str, List[Dict[str, Any]]] = {}

    doc_metadata_map = build_doc_metadata_map(doc_items)

    async def extractor_with_metadata(content: str, job_id: UUID, doc_uuid: UUID):
        doc_metadata = doc_metadata_map.get(str(doc_uuid))
        return await extract_object_classes_raw(content, job_id, doc_uuid, doc_metadata)

    # Process all documents in parallel using the generic function
    results = await process_documents_in_parallel(
        doc_items=doc_items,
        job_id=job_id,
        extractor=extractor_with_metadata,
        logger_scope="Digester:ObjectClasses",
    )

    # Collect results from all documents
    for raw_classes, has_relevant_data, doc_uuid in results:
        logger.info(
            "[Digester:ObjectClasses] Document %s: extracted %s object classes",
            doc_uuid,
            len(raw_classes),
        )
        # For each object class, track which document chunks it appears in
        # Only add chunks that are specifically relevant to this object class
        for obj_class in raw_classes:
            class_name = obj_class.name.strip().lower()
            if class_name not in class_to_chunks:
                class_to_chunks[class_name] = []

            if obj_class.relevant_chunks:
                class_to_chunks[class_name].extend(obj_class.relevant_chunks)
            else:
                class_to_chunks[class_name].append({"docUuid": str(doc_uuid)})

        all_object_classes.extend(raw_classes)
        if has_relevant_data:
            all_relevant_chunks.append({"docUuid": str(doc_uuid)})

    logger.info(
        "[Digester:ObjectClasses] Processing complete. Total: %s object classes from %s documents. "
        "Starting deduplication and sorting...",
        len(all_object_classes),
        len(doc_items),
    )
    final_result = await deduplicate_and_sort_object_classes(
        all_object_classes, job_id, filter_relevancy, min_relevancy_level, class_to_chunks
    )

    return {
        "result": final_result.model_dump(by_alias=True) if hasattr(final_result, "model_dump") else final_result,
        "relevantChunks": all_relevant_chunks,
    }


async def extract_auth(doc_items: List[dict], job_id: UUID):
    """
    Extract authentication info from multiple documentation items and return merged result with metadata.

    Step 1: Extract raw auth info from each document (per UUID) - processes documents in parallel
    Step 2: Merge, deduplicate and sort ALL auth info together
    """
    all_auth_info = []
    all_relevant_chunks: List[Dict[str, Any]] = []

    doc_metadata_map = build_doc_metadata_map(doc_items)

    async def extractor_with_metadata(content: str, job_id: UUID, doc_uuid: UUID):
        doc_metadata = doc_metadata_map.get(str(doc_uuid))
        return await extract_auth_raw(content, job_id, doc_uuid, doc_metadata)

    # Process all documents in parallel using the generic function
    results = await process_documents_in_parallel(
        doc_items=doc_items,
        job_id=job_id,
        extractor=extractor_with_metadata,
        logger_scope="Digester:Auth",
    )

    # Collect results from all documents
    for raw_auth, has_relevant_data, doc_uuid in results:
        logger.info(
            "[Digester:Auth] Document %s: extracted %s auth items",
            doc_uuid,
            len(raw_auth),
        )
        all_auth_info.extend(raw_auth)
        if has_relevant_data:
            all_relevant_chunks.append({"docUuid": str(doc_uuid)})

    logger.info(
        "[Digester:Auth] Processing complete. Total: %s auth items from %s documents. "
        "Starting deduplication and sorting...",
        len(all_auth_info),
        len(doc_items),
    )
    final_result = await deduplicate_and_sort_auth(all_auth_info, job_id)

    return {
        "result": final_result.model_dump(by_alias=True) if hasattr(final_result, "model_dump") else final_result,
        "relevantChunks": all_relevant_chunks,
    }


async def extract_info_metadata(doc_items: List[dict], job_id: UUID):
    """
    Extract metadata from multiple documentation items, aggregating sequentially across documents.
    The final aggregated result from doc N is used as the initial state for doc N+1.
    """
    all_relevant_chunks: List[Dict[str, Any]] = []
    total_docs = len(doc_items)
    doc_metadata_map = build_doc_metadata_map(doc_items)

    await update_job_progress(
        job_id, total_processing=total_docs, processing_completed=0, message="Processing documents"
    )

    aggregated_result: Any = None

    for idx, doc_item in enumerate(doc_items, 1):
        doc_uuid = doc_item["uuid"]
        doc_content = doc_item["content"]
        doc_metadata = doc_metadata_map.get(str(doc_uuid))

        logger.info("[Digester:InfoMetadata] Processing document %s/%s (UUID: %s)", idx, total_docs, doc_uuid)

        raw_result, has_relevant_data = await _extract_info_metadata(
            doc_content,
            job_id,
            doc_uuid,
            initial_aggregated=aggregated_result,
            doc_metadata=doc_metadata,
        )

        aggregated_result = raw_result

        logger.info(
            "[Digester:InfoMetadata] Document %s: processed",
            doc_uuid,
        )

        if has_relevant_data:
            all_relevant_chunks.append({"docUuid": str(doc_uuid)})

        await increment_processed_documents(job_id, delta=1)

    # All documents processed, now finalizing
    logger.info("[Digester:InfoMetadata] All documents processed. Finalizing aggregated result.")
    await update_job_progress(job_id, stage="aggregation_finished", message="Extraction complete; finalizing")

    if hasattr(aggregated_result, "model_dump"):
        merged_result: Dict[str, Any] = cast(Dict[str, Any], aggregated_result.model_dump(by_alias=True))
    else:
        merged_result = cast(Dict[str, Any], aggregated_result or {})

    if _is_empty_info_result_payload(merged_result):
        merged_result = {"infoMetadata": None}

    return {
        "result": merged_result,
        "relevantChunks": all_relevant_chunks,
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
        relevant_chunks: List of {docUuid} dicts indicating which documents to process
        job_id: Job ID for progress tracking
    """
    selected_docs, doc_uuids = select_doc_chunks(doc_items, relevant_chunks, "Digester:Attributes")

    if not selected_docs:
        logger.warning(f"[Digester:Attributes] No relevant chunks found for {object_class}")
        return {"result": {"attributes": {}}, "relevantChunks": []}

    doc_metadata_map = build_doc_metadata_map(doc_items)

    api_type = await get_api_type_from_session(session_id)
    is_scim = is_scim_api(api_type)

    logger.info(
        "%s",
        protocol_selection_message(
            "Digester:Attributes",
            is_scim=is_scim,
            scim_mode="guided extraction",
            rest_mode="standard REST extraction",
            object_class=object_class,
        ),
    )

    if is_scim:
        result = await extract_scim_attributes(selected_docs, object_class, job_id, doc_uuids, doc_metadata_map)
    else:
        result = await _extract_rest_attributes(selected_docs, object_class, job_id, doc_uuids, doc_metadata_map)

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
        relevant_chunks: List of {docUuid} dicts indicating which documents to process
        job_id: Job ID for progress tracking
        base_api_url: Base API URL for endpoint extraction
    """

    api_type = await get_api_type_from_session(session_id)
    is_scim = is_scim_api(api_type)

    logger.info(
        "%s",
        protocol_selection_message(
            "Digester:Endpoints",
            is_scim=is_scim,
            scim_mode="deterministic endpoint pregeneration",
            rest_mode="standard REST extraction",
            object_class=object_class,
        ),
    )

    if is_scim:
        result = await pregenerate_scim_endpoints(
            session_id=session_id,
            object_class=object_class,
            base_api_url=base_api_url,
            job_id=job_id,
            relevant_chunks=relevant_chunks,
        )
    else:
        selected_docs, doc_uuids = select_doc_chunks(doc_items, relevant_chunks, "Digester:Endpoints")

        if not selected_docs:
            logger.warning(f"[Digester:Endpoints] No relevant chunks found for {object_class}")
            return {"result": {"endpoints": []}, "relevantChunks": []}

        doc_metadata_map = build_doc_metadata_map(doc_items)

        # Log chunk processing details
        total_chunks = len(selected_docs)
        logger.info(
            "[Digester:Endpoints] Processing %d pre-selected chunks for %s (from original indices: %s)",
            total_chunks,
            object_class,
            doc_uuids,
        )

        result = await _extract_rest_endpoints(
            selected_docs, object_class, job_id, base_api_url, doc_uuids, doc_metadata_map
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


async def extract_relations(doc_items: List[dict], relevant_object_class: str, job_id: UUID):
    """Extract relations from multiple documentation items."""

    doc_metadata_map = build_doc_metadata_map(doc_items)

    def extractor(content: str, jid: UUID, doc_id: UUID):
        doc_metadata = doc_metadata_map.get(str(doc_id))
        return _extract_relations(content, relevant_object_class, jid, doc_id, doc_metadata)

    def per_doc_count(d: Dict[str, Any]) -> int:
        return len(cast(List[dict], d.get("relations", [])))

    return await _process_over_documents(
        doc_items=doc_items,
        job_id=job_id,
        extractor=extractor,
        merger=merge_relations_results,
        logger_scope="Digester:Relations",
        per_doc_count=per_doc_count,
    )
