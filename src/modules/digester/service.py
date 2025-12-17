# Copyright (c) 2025 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Any, Callable, Dict, Iterable, List, Tuple, cast
from uuid import UUID

from ...common.chunks import normalize_to_text, split_text_with_token_overlap
from ...common.jobs import increment_processed_documents, update_job_progress
from ...common.session.session import SessionManager
from .utils.auth import deduplicate_and_sort_auth, extract_auth_raw
from .utils.endpoints import extract_endpoints as _extract_endpoints
from .utils.info import extract_info_metadata as _extract_info_metadata
from .utils.merges import (
    merge_relations_results,
)
from .utils.object_class import deduplicate_and_sort_object_classes, extract_object_classes_raw
from .utils.object_class_attributes import extract_attributes as _extract_attributes
from .utils.parallel_docs import process_documents_in_parallel
from .utils.relations import extract_relations as _extract_relations

logger = logging.getLogger(__name__)


def _collect_relevant_chunks(doc_uuid: UUID, indices: Iterable[int]) -> List[Dict[str, Any]]:
    return [{"docUuid": doc_uuid, "chunkIndex": idx} for idx in indices]


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
    for raw_result, relevant_indices, doc_uuid in results:
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
            logger.info(
                f"[{logger_scope}] Document {doc_uuid}: extracted {count} items from {len(relevant_indices)} relevant chunks"
            )

        if result_data:
            all_results.append(result_data)
        all_relevant_chunks.extend(_collect_relevant_chunks(doc_uuid, relevant_indices))

    merged_result: Dict[str, Any] = merger(all_results)

    return {
        "result": merged_result,
        "relevantChunks": all_relevant_chunks,
    }


async def extract_object_classes(doc_items: List[dict], filter_relevancy: bool, min_relevancy_level: str, job_id: UUID):
    """
    Extract object classes from multiple documentation items and return merged result with metadata.

    Step 1: Extract raw object classes from each document (per UUID) - processes documents in parallel
    Step 2: Merge, deduplicate and sort ALL object classes together
    """
    all_object_classes = []
    all_relevant_chunks: List[Dict[str, Any]] = []
    class_to_chunks: Dict[str, List[Dict[str, Any]]] = {}

    # Create a wrapper extractor that includes metadata
    async def extractor_with_metadata(content: str, job_id: UUID, doc_uuid: UUID):
        # Find the original doc_item to get metadata
        doc_metadata = None
        for doc_item in doc_items:
            if doc_item.get("uuid") == str(doc_uuid):
                # Extract summary and @metadata
                doc_metadata = {
                    "summary": doc_item.get("summary"),
                    "@metadata": doc_item.get("@metadata", {}),
                }
                break
        return await extract_object_classes_raw(content, job_id, doc_uuid, doc_metadata)

    # Process all documents in parallel using the generic function
    results = await process_documents_in_parallel(
        doc_items=doc_items,
        job_id=job_id,
        extractor=extractor_with_metadata,
        logger_scope="Digester:ObjectClasses",
    )

    # Collect results from all documents
    for raw_classes, relevant_indices, doc_uuid in results:
        logger.info(
            "[Digester:ObjectClasses] Document %s: extracted %s object classes from %s relevant chunks",
            doc_uuid,
            len(raw_classes),
            len(relevant_indices),
        )
        # For each object class, track which document chunks it appears in
        # Only add chunks that are specifically relevant to this object class
        for obj_class in raw_classes:
            class_name = obj_class.name.strip().lower()
            if class_name not in class_to_chunks:
                class_to_chunks[class_name] = []

            # If the object class already has relevant_chunks set during extraction, use those
            # Otherwise fall back to all relevant_indices (for backward compatibility)
            if obj_class.relevant_chunks:
                class_to_chunks[class_name].extend(obj_class.relevant_chunks)
            else:
                # Fallback: add all relevant chunks (old behavior)
                for chunk_idx in relevant_indices:
                    class_to_chunks[class_name].append({"docUuid": doc_uuid, "chunkIndex": chunk_idx})

        all_object_classes.extend(raw_classes)
        all_relevant_chunks.extend(_collect_relevant_chunks(doc_uuid, relevant_indices))

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

    # Create a wrapper extractor that includes metadata
    async def extractor_with_metadata(content: str, job_id: UUID, doc_uuid: UUID):
        # Find the original doc_item to get metadata
        doc_metadata = None
        for doc_item in doc_items:
            if doc_item.get("uuid") == str(doc_uuid):
                doc_metadata = {
                    "summary": doc_item.get("summary"),
                    "@metadata": doc_item.get("@metadata", {}),
                }
                break
        return await extract_auth_raw(content, job_id, doc_uuid, doc_metadata)

    # Process all documents in parallel using the generic function
    results = await process_documents_in_parallel(
        doc_items=doc_items,
        job_id=job_id,
        extractor=extractor_with_metadata,
        logger_scope="Digester:Auth",
    )

    # Collect results from all documents
    for raw_auth, relevant_indices, doc_uuid in results:
        logger.info(
            "[Digester:Auth] Document %s: extracted %s auth items from %s relevant chunks",
            doc_uuid,
            len(raw_auth),
            len(relevant_indices),
        )
        all_auth_info.extend(raw_auth)
        all_relevant_chunks.extend(_collect_relevant_chunks(doc_uuid, relevant_indices))

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

    update_job_progress(job_id, total_documents=total_docs, processed_documents=0, message="Processing documents")

    aggregated_result: Any = None

    for idx, doc_item in enumerate(doc_items, 1):
        doc_uuid = doc_item["uuid"]
        doc_content = doc_item["content"]

        logger.info("[Digester:InfoMetadata] Processing document %s/%s (UUID: %s)", idx, total_docs, doc_uuid)

        raw_result, relevant_indices = await _extract_info_metadata(
            doc_content, job_id, doc_uuid, initial_aggregated=aggregated_result
        )

        aggregated_result = raw_result

        logger.info(
            "[Digester:InfoMetadata] Document %s: processed with %s relevant chunks",
            doc_uuid,
            len(relevant_indices),
        )

        all_relevant_chunks.extend(_collect_relevant_chunks(doc_uuid, relevant_indices))

        increment_processed_documents(job_id, delta=1)

    if hasattr(aggregated_result, "model_dump"):
        merged_result: Dict[str, Any] = cast(Dict[str, Any], aggregated_result.model_dump(by_alias=True))
    else:
        merged_result = cast(Dict[str, Any], aggregated_result or {})

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

    Args:
        doc_items: Full documentation items
        object_class: Name of the object class
        session_id: Session ID
        relevant_chunks: List of {docUuid, chunkIndex} pairs indicating which chunks to process
        job_id: Job ID for progress tracking
    """
    # Extract specific chunks directly without re-chunking
    selected_chunks, chunk_details = _extract_specific_chunks(doc_items, relevant_chunks, "Digester:Attributes")

    if not selected_chunks:
        logger.warning(f"[Digester:Attributes] No relevant chunks found for {object_class}")
        return {"result": {"attributes": {}}, "relevantChunks": []}

    # Build metadata map: doc_uuid -> {summary, @metadata}
    doc_metadata_map = {}
    for doc_item in doc_items:
        doc_uuid = doc_item.get("uuid")
        if doc_uuid:
            doc_metadata_map[doc_uuid] = {
                "summary": doc_item.get("summary"),
                "@metadata": doc_item.get("@metadata", {}),
            }

    # Log chunk processing details
    total_chunks = len(selected_chunks)
    logger.info(
        "[Digester:Attributes] Processing %d pre-selected chunks for %s (from original indices: %s)",
        total_chunks,
        object_class,
        chunk_details,
    )

    result = await _extract_attributes(selected_chunks, object_class, job_id, chunk_details, doc_metadata_map)

    try:
        logger.info(f"[Digester:Attributes] Attempting to update object class '{object_class}' with attributes")
        object_classes_output = SessionManager.get_session_data(session_id, "objectClassesOutput")

        if not object_classes_output:
            logger.warning(f"[Digester:Attributes] No objectClassesOutput found in session {session_id}")
            return result

        if not isinstance(object_classes_output, dict):
            logger.warning(f"[Digester:Attributes] objectClassesOutput is not a dict: {type(object_classes_output)}")
            return result

        object_classes = object_classes_output.get("objectClasses", [])
        if not isinstance(object_classes, list):
            logger.warning(f"[Digester:Attributes] objectClasses is not a list: {type(object_classes)}")
            return result

        # Find the matching object class (case-insensitive)
        normalized_name = object_class.strip().lower()
        found = False

        for obj_class in object_classes:
            if isinstance(obj_class, dict) and obj_class.get("name", "").strip().lower() == normalized_name:
                found = True
                # Update the attributes field
                attributes_dict = {}
                if result and isinstance(result, dict):
                    result_data = result.get("result", result)
                    attributes_dict = result_data.get("attributes", {})

                    logger.info(f"[Digester:Attributes] Found {len(attributes_dict)} attributes in result")

                obj_class["attributes"] = attributes_dict
                logger.info(
                    f"[Digester:Attributes] Updated object class '{obj_class.get('name')}' with {len(attributes_dict)} attributes"
                )
                break

        if not found:
            logger.warning(
                f"[Digester:Attributes] Object class '{object_class}' (normalized: '{normalized_name}') not found in objectClasses"
            )
            available_classes = [oc.get("name", "?") for oc in object_classes if isinstance(oc, dict)]
            logger.info(f"[Digester:Attributes] Available classes: {available_classes}")
            return result

        # Save back to session
        logger.info("[Digester:Attributes] Saving updated attributes back to session")
        SessionManager.update_session(session_id, {"objectClassesOutput": object_classes_output})
        logger.info("[Digester:Attributes] Successfully saved attributes to session")

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

    Args:
        doc_items: Full documentation items
        object_class: Name of the object class
        session_id: Session ID
        relevant_chunks: List of {docUuid, chunkIndex} pairs indicating which chunks to process
        job_id: Job ID for progress tracking
        base_api_url: Base API URL for endpoint extraction
    """
    # Extract specific chunks directly without re-chunking
    selected_chunks, chunk_details = _extract_specific_chunks(doc_items, relevant_chunks, "Digester:Endpoints")

    if not selected_chunks:
        logger.warning(f"[Digester:Endpoints] No relevant chunks found for {object_class}")
        return {"result": {"endpoints": []}, "relevantChunks": []}

    # Build metadata map: doc_uuid -> {summary, @metadata}
    doc_metadata_map = {}
    for doc_item in doc_items:
        doc_uuid = doc_item.get("uuid")
        if doc_uuid:
            doc_metadata_map[doc_uuid] = {
                "summary": doc_item.get("summary"),
                "@metadata": doc_item.get("@metadata", {}),
            }

    # Log chunk processing details
    total_chunks = len(selected_chunks)
    logger.info(
        "[Digester:Endpoints] Processing %d pre-selected chunks for %s (from original indices: %s)",
        total_chunks,
        object_class,
        chunk_details,
    )

    # Process each selected chunk through endpoint extraction
    result = await _extract_endpoints(
        selected_chunks, object_class, job_id, base_api_url, chunk_details, doc_metadata_map
    )

    # Now update the specific object class in objectClassesOutput
    try:
        object_classes_output = SessionManager.get_session_data(session_id, "objectClassesOutput")
        if object_classes_output and isinstance(object_classes_output, dict):
            object_classes = object_classes_output.get("objectClasses", [])
            if isinstance(object_classes, list):
                # Find the matching object class (case-insensitive)
                normalized_name = object_class.strip().lower()
                for obj_class in object_classes:
                    if isinstance(obj_class, dict) and obj_class.get("name", "").strip().lower() == normalized_name:
                        # Update the endpoint field - convert Pydantic models to dicts
                        endpoints_list = []
                        if result and isinstance(result, dict):
                            result_data = result.get("result", result)
                            raw_endpoints = result_data.get("endpoints", [])

                            # Convert each endpoint to dict (handle both Pydantic models and dicts)
                            for ep in raw_endpoints:
                                if hasattr(ep, "model_dump"):
                                    endpoints_list.append(ep.model_dump(by_alias=True))
                                elif isinstance(ep, dict):
                                    endpoints_list.append(ep)

                        obj_class["endpoints"] = endpoints_list
                        logger.info(
                            f"[Digester:Endpoints] Updated object class '{obj_class.get('name')}' with {len(endpoints_list)} endpoints"
                        )
                        break

                # Save back to session
                SessionManager.update_session(session_id, {"objectClassesOutput": object_classes_output})
    except Exception as e:
        logger.warning(f"[Digester:Endpoints] Failed to update object class with endpoints: {e}")

    return result


def _extract_specific_chunks(
    doc_items: List[dict], relevant_chunks: List[Dict[str, Any]], log_prefix: str = "Digester"
) -> Tuple[List[str], List[Tuple[int, str]]]:
    """
    Extract the exact chunks specified by relevant_chunks from doc_items.
    Does NOT re-chunk - returns the original chunks at the specified indices.

    Args:
        doc_items: Original documentation items with full content
        relevant_chunks: List of {docUuid, chunkIndex} indicating which chunks to extract
        log_prefix: Prefix for log messages (e.g., "Digester:Endpoints", "Digester:Attributes")

    Returns:
        Tuple of:
        - List of selected chunk texts
        - List of (original_chunk_index, doc_uuid) for logging
    """

    # Group chunks by document UUID and maintain order
    chunks_by_doc: Dict[str, List[int]] = {}
    ordered_doc_uuids: List[str] = []

    for chunk_info in relevant_chunks:
        doc_uuid = str(chunk_info.get("docUuid", ""))
        chunk_idx = int(chunk_info.get("chunkIndex", -1))
        if doc_uuid and chunk_idx >= 0:
            if doc_uuid not in chunks_by_doc:
                chunks_by_doc[doc_uuid] = []
                ordered_doc_uuids.append(doc_uuid)
            chunks_by_doc[doc_uuid].append(chunk_idx)

    # Sort chunk indices for each document
    for doc_uuid in chunks_by_doc:
        chunks_by_doc[doc_uuid].sort()

    # Extract the specific chunks
    selected_chunks: List[str] = []
    chunk_details: List[Tuple[int, str]] = []

    for doc_item in doc_items:
        doc_uuid = doc_item.get("uuid", "")
        if doc_uuid not in chunks_by_doc:
            continue

        # Split the document into chunks
        text = normalize_to_text(doc_item.get("content", ""))
        all_chunks: List[tuple[str, int]] = split_text_with_token_overlap(text)

        # Get only the relevant chunk indices for this document
        selected_indices = chunks_by_doc[doc_uuid]

        logger.info(
            "[%s] Doc %s -> %d total chunks, selected indices: %s",
            log_prefix,
            doc_uuid,
            len(all_chunks),
            selected_indices,
        )

        # Collect the exact chunks at specified indices
        for idx in selected_indices:
            if 0 <= idx < len(all_chunks):
                selected_chunks.append(all_chunks[idx][0])
                chunk_details.append((idx, doc_uuid))

    total_selected = len(selected_chunks)
    total_docs = len(chunks_by_doc)
    logger.info(
        "[%s] Extracted %d chunks from %d documents (indices preserved)",
        log_prefix,
        total_selected,
        total_docs,
    )

    return selected_chunks, chunk_details


async def extract_relations(doc_items: List[dict], relevant_object_class: str, job_id: UUID):
    """Extract relations from multiple documentation items."""

    def extractor(content: str, jid: UUID, doc_id: UUID):
        # Find the original doc_item to get metadata
        doc_metadata = None
        for doc_item in doc_items:
            if doc_item.get("uuid") == str(doc_id):
                doc_metadata = {
                    "summary": doc_item.get("summary"),
                    "@metadata": doc_item.get("@metadata", {}),
                }
                break
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
