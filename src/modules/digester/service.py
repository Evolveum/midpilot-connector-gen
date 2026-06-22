# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
from collections import Counter
from typing import Any, Dict, List, Set, Tuple, cast
from uuid import UUID

from src.common.chunk_filter.filter import filter_documentation_items
from src.common.enums import ApiType, JobStage
from src.common.jobs import update_job_progress
from src.common.utils.normalize import normalize_endpoint_key
from src.common.utils.session_info_metadata import resolve_effective_api_type
from src.modules.digester.aggregation.merges import (
    merge_api_type,
    merge_info_metadata,
    merge_relations_results,
)
from src.modules.digester.entities.object_classes import (
    extract_attributes_from_result,
    extract_endpoints_from_result,
    update_object_class_field_in_session,
)
from src.modules.digester.extraction.chunk_extraction import process_over_chunks, run_doc_extractors_concurrently
from src.modules.digester.extraction.metadata_helper import build_doc_metadata_map
from src.modules.digester.extractors.apitype import extract_api_type as _extract_api_type

# Shared extractors
from src.modules.digester.extractors.auth import (
    build_auth_items,
    deduplicate_auth,
    extract_auth_raw,
    sort_auth_by_importance,
)
from src.modules.digester.extractors.connectivity_endpoint import (
    extract_connectivity_endpoint_raw as _extract_connectivity_endpoint_raw,
)
from src.modules.digester.extractors.connectivity_endpoint import (
    merge_and_rank_connectivity_endpoint_candidates,
)
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
from src.modules.digester.extractors.sql.attributes import extract_sql_attributes
from src.modules.digester.extractors.sql.object_class import extract_sql_object_classes
from src.modules.digester.extractors.sql.tables import extract_sql_tables
from src.modules.digester.schemas import (
    ApiTypeResponse,
    ExtractedConnectivityEndpointInfo,
    InfoExtractionResponse,
    InfoMetadataExtraction,
)
from src.modules.digester.selection.criteria import CONNECTIVITY_ENDPOINT_FALLBACK_CRITERIA, DEFAULT_CRITERIA
from src.modules.digester.selection.doc_chunk import (
    build_chunk_id_to_doc_id,
    build_relevant_chunks_from_doc_items,
    chunk_ids_from_relevant_chunks,
    exclude_doc_items_by_chunk_id,
    select_doc_chunks,
)

logger = logging.getLogger(__name__)


def _auth_type_counts(auth_items: Any) -> Dict[str, int]:
    items = getattr(auth_items, "auth", auth_items)
    if not items:
        return {}

    type_counts: Counter[str] = Counter()
    for item in items:
        raw_type = item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
        type_value = getattr(raw_type, "value", raw_type)
        type_counts[str(type_value or "unknown")] += 1
    return dict(sorted(type_counts.items()))


async def extract_object_classes(
    doc_items: List[dict],
    job_id: UUID,
    session_id: UUID,
    api_type_override: ApiType | None = None,
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

    Returns:
        Dictionary with result and relevantDocumentations
    """
    protocol = await resolve_effective_api_type(session_id, api_type_override)
    if protocol == ApiType.SQL:
        return await extract_sql_object_classes(doc_items, job_id)

    if protocol == ApiType.SCIM:
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
    discovery_results = await run_doc_extractors_concurrently(
        chunk_items=doc_items,
        job_id=job_id,
        extractor=extractor_with_metadata,
        logger_scope="Digester:Auth",
    )

    # Collect results from all chunks
    for raw_auth, has_relevant_data, chunk_id in discovery_results:
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
        "[Digester:Auth] Auth discovery complete. Total: %s auth items from %s documents. Starting deduplication",
        len(all_auth_info),
        len(doc_items),
    )
    await update_job_progress(job_id, stage=JobStage.discovery_finished, message="Auth discovery finished")
    deduplicated_results = await deduplicate_auth(all_auth_info, job_id)

    logger.info(
        "[Digester:Auth] Deduplication complete. Total: %s unique auth items. Type counts: %s",
        len(deduplicated_results),
        _auth_type_counts(deduplicated_results),
    )

    built_auth_items = await build_auth_items(deduplicated_results, job_id)

    logger.info(
        "[Digester:Auth] Build complete. Total: %s built auth items. Type counts: %s",
        len(built_auth_items),
        _auth_type_counts(built_auth_items),
    )

    final_deduplicated_results = await deduplicate_auth(built_auth_items, job_id)

    logger.info(
        "[Digester:Auth] Deduplication complete. Total: %s unique auth items. Type counts: %s",
        len(final_deduplicated_results),
        _auth_type_counts(final_deduplicated_results),
    )

    sorted_auth_items = await sort_auth_by_importance(final_deduplicated_results, job_id)

    logger.info(
        "[Digester:Auth] Sorting complete. Total: %s sorted auth items. Type counts: %s",
        len(sorted_auth_items.auth) if hasattr(sorted_auth_items, "auth") and sorted_auth_items.auth else 0,
        _auth_type_counts(sorted_auth_items),
    )

    return {
        "result": sorted_auth_items.model_dump(by_alias=True)
        if hasattr(sorted_auth_items, "model_dump")
        else sorted_auth_items,
        "relevantDocumentations": all_relevant_chunks,
    }


def _endpoint_result_has_items(extraction_result: Dict[str, Any]) -> bool:
    return len(extract_endpoints_from_result(extraction_result)) > 0


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


async def _retry_attributes_with_default_criteria(
    doc_items: List[dict],
    object_class: str,
    session_id: UUID,
    job_id: UUID,
    old_relevant_chunks: List[Dict[str, Any]],
    chunk_metadata_map: Dict[str, Any],
    chunk_id_to_doc_id: Dict[str, str],
    is_scim: bool = False,
) -> Dict[str, Any] | None:
    fallback_doc_items = await filter_documentation_items(DEFAULT_CRITERIA, session_id)
    if not fallback_doc_items:
        logger.info(
            "[Digester:Attributes] DEFAULT_CRITERIA matched no documentation for session %s; keeping empty attribute result",
            session_id,
        )
        return None

    primary_chunk_ids = chunk_ids_from_relevant_chunks(old_relevant_chunks)
    fallback_relevant_chunks = build_relevant_chunks_from_doc_items(fallback_doc_items)
    fallback_chunk_ids_set = chunk_ids_from_relevant_chunks(fallback_relevant_chunks)
    if primary_chunk_ids and primary_chunk_ids == fallback_chunk_ids_set:
        logger.info(
            "[Digester:Attributes] DEFAULT_CRITERIA matched same chunks for session %s, object class %s; skipping retry",
            session_id,
            object_class,
        )
        return None

    fallback_doc_items_filtered = exclude_doc_items_by_chunk_id(fallback_doc_items, primary_chunk_ids)
    fallback_relevant_chunks = build_relevant_chunks_from_doc_items(fallback_doc_items_filtered)
    if not fallback_relevant_chunks:
        logger.info(
            "[Digester:Attributes] DEFAULT_CRITERIA produced no new chunks for session %s; keeping empty attribute result",
            session_id,
        )
        return None

    fallback_selected_content, fallback_chunk_ids = select_doc_chunks(
        fallback_doc_items_filtered, fallback_relevant_chunks, "Digester:Attributes"
    )

    if is_scim:
        fallback_result = await extract_scim_attributes(
            fallback_selected_content,
            object_class,
            job_id,
            fallback_chunk_ids,
            chunk_metadata_map,
            chunk_id_to_doc_id,
        )
    else:
        fallback_result = await _extract_rest_attributes(
            fallback_selected_content,
            object_class,
            job_id,
            fallback_chunk_ids,
            chunk_metadata_map,
            chunk_id_to_doc_id,
        )

    if not fallback_result:
        logger.info(
            "[Digester:Attributes] DEFAULT_CRITERIA chunks could not be selected for session %s; keeping empty attribute result",
            session_id,
        )

    return fallback_result


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

    Step 1: Run two independent per-chunk extractors concurrently:
            - info metadata (name, versions, base endpoints, database name),
            - apiType (REST/SCIM/SQL protocol detection) as a standalone LLM call.
    Step 2: Merge both sets of candidates using threshold-based heuristics and join the
            detected apiType into one final InfoResponse payload.
    """
    all_info_candidates: List[InfoMetadataExtraction] = []
    all_api_type_candidates: List[ApiTypeResponse] = []
    relevant_chunks_by_id: Dict[str, Dict[str, str]] = {}
    chunk_id_to_doc_id = build_chunk_id_to_doc_id(doc_items)
    chunk_metadata_map = build_doc_metadata_map(doc_items)

    async def info_extractor_with_metadata(content: str, job_id: UUID, chunk_id: UUID):
        chunk_metadata = chunk_metadata_map.get(str(chunk_id))
        return await _extract_info_metadata(content, job_id, chunk_id, chunk_metadata)

    async def api_type_extractor_with_metadata(content: str, job_id: UUID, chunk_id: UUID):
        chunk_metadata = chunk_metadata_map.get(str(chunk_id))
        return await _extract_api_type(content, job_id, chunk_id, chunk_metadata)

    def track_relevant_chunk(chunk_id: UUID) -> None:
        chunk_id_str = str(chunk_id)
        if chunk_id_str in relevant_chunks_by_id:
            return
        doc_id = chunk_id_to_doc_id.get(chunk_id_str)
        if doc_id:
            relevant_chunks_by_id[chunk_id_str] = {"doc_id": doc_id, "chunk_id": chunk_id_str}
        else:
            logger.warning(
                "[Digester:InfoMetadata] Missing docId for chunk %s, skipping relevant chunk mapping",
                chunk_id_str,
            )

    # Info and apiType run as two concurrent passes over the same chunks, i.e. two LLM calls
    # per chunk. Set the combined total once here so progress only reaches 100% once both
    # passes finish; each pass then increments completed counts (set_total=False avoids
    # either pass overwriting the shared total).
    await update_job_progress(
        job_id,
        total_processing=len(doc_items) * 2,
        message="Processing chunks",
    )

    info_results, api_type_results = await asyncio.gather(
        run_doc_extractors_concurrently(
            chunk_items=doc_items,
            job_id=job_id,
            extractor=info_extractor_with_metadata,
            logger_scope="Digester:InfoMetadata",
            set_total=False,
        ),
        run_doc_extractors_concurrently(
            chunk_items=doc_items,
            job_id=job_id,
            extractor=api_type_extractor_with_metadata,
            logger_scope="Digester:ApiType",
            set_total=False,
        ),
    )

    for raw_infos, has_relevant_data, chunk_id in info_results:
        normalized_infos: List[InfoMetadataExtraction] = []

        if isinstance(raw_infos, list):
            for item in raw_infos:
                if isinstance(item, InfoMetadataExtraction):
                    normalized_infos.append(item)
                    continue
                if isinstance(item, InfoExtractionResponse):
                    if item.info_metadata is not None:
                        normalized_infos.append(item.info_metadata)
                    continue
                if isinstance(item, dict):
                    try:
                        parsed_info = InfoExtractionResponse.model_validate(item).info_metadata
                        if parsed_info is not None:
                            normalized_infos.append(parsed_info)
                        continue
                    except Exception as response_exc:
                        try:
                            normalized_infos.append(InfoMetadataExtraction.model_validate(item))
                        except Exception as metadata_exc:
                            logger.warning(
                                "[Digester:InfoMetadata] Dropping invalid metadata item from chunk %s after "
                                "InfoExtractionResponse and InfoMetadataExtraction validation failed. errors=%s/%s",
                                chunk_id,
                                type(response_exc).__name__,
                                type(metadata_exc).__name__,
                            )
                            continue
        elif isinstance(raw_infos, InfoMetadataExtraction):
            normalized_infos.append(raw_infos)
        elif isinstance(raw_infos, InfoExtractionResponse):
            if raw_infos.info_metadata is not None:
                normalized_infos.append(raw_infos.info_metadata)
        elif isinstance(raw_infos, dict):
            try:
                parsed_info = InfoExtractionResponse.model_validate(raw_infos).info_metadata
                if parsed_info is not None:
                    normalized_infos.append(parsed_info)
            except Exception as response_exc:
                try:
                    normalized_infos.append(InfoMetadataExtraction.model_validate(raw_infos))
                except Exception as metadata_exc:
                    logger.warning(
                        "[Digester:InfoMetadata] Dropping invalid metadata payload from chunk %s after "
                        "InfoExtractionResponse and InfoMetadataExtraction validation failed. errors=%s/%s",
                        chunk_id,
                        type(response_exc).__name__,
                        type(metadata_exc).__name__,
                    )

        logger.info(
            "[Digester:InfoMetadata] Chunk %s: extracted %s metadata candidates",
            chunk_id,
            len(normalized_infos),
        )
        all_info_candidates.extend(normalized_infos)

        if has_relevant_data:
            track_relevant_chunk(chunk_id)

    for raw_api_types, has_relevant_data, chunk_id in api_type_results:
        normalized_api_types: List[ApiTypeResponse] = []

        candidates = raw_api_types if isinstance(raw_api_types, list) else [raw_api_types]
        for item in candidates:
            if isinstance(item, ApiTypeResponse):
                normalized_api_types.append(item)
            elif isinstance(item, dict):
                try:
                    normalized_api_types.append(ApiTypeResponse.model_validate(item))
                except Exception as exc:
                    logger.warning(
                        "[Digester:ApiType] Dropping invalid apiType payload from chunk %s: %s",
                        chunk_id,
                        type(exc).__name__,
                    )

        logger.info(
            "[Digester:ApiType] Chunk %s: extracted %s apiType candidates",
            chunk_id,
            len(normalized_api_types),
        )
        all_api_type_candidates.extend(normalized_api_types)

        if has_relevant_data:
            track_relevant_chunk(chunk_id)

    logger.info(
        "[Digester:InfoMetadata] Processing complete. Total: %s info candidates and %s apiType candidates "
        "from %s chunks. Starting heuristic merge...",
        len(all_info_candidates),
        len(all_api_type_candidates),
        len(doc_items),
    )

    api_types = merge_api_type(all_api_type_candidates, total_items=len(doc_items))
    merged_result = merge_info_metadata(all_info_candidates, total_items=len(doc_items), api_types=api_types)
    await update_job_progress(job_id, stage="aggregation_finished", message="Extraction complete; finalizing")

    return {
        "result": merged_result,
        "relevantDocumentations": list(relevant_chunks_by_id.values()),
    }


def _connectivity_endpoint_result_has_item(extraction_result: Dict[str, Any]) -> bool:
    result_data = extraction_result.get("result", extraction_result)
    return isinstance(result_data, dict) and bool(result_data.get("endpoints"))


async def _extract_connectivity_endpoint_from_doc_items(
    doc_items: List[dict],
    job_id: UUID,
    base_api_url: str,
) -> Dict[str, Any]:
    if not doc_items:
        return {"result": {"endpoints": []}, "relevantDocumentations": []}

    all_candidates: List[ExtractedConnectivityEndpointInfo] = []
    all_relevant_chunks: List[Dict[str, Any]] = []
    endpoint_chunk_pairs: Dict[Tuple[str, str], Set[Tuple[str, str]]] = {}
    chunk_id_to_doc_id = build_chunk_id_to_doc_id(doc_items)
    chunk_metadata_map = build_doc_metadata_map(doc_items)

    async def extractor_with_metadata(content: str, job_id: UUID, chunk_id: UUID):
        chunk_metadata = chunk_metadata_map.get(str(chunk_id))
        return await _extract_connectivity_endpoint_raw(
            content,
            job_id,
            chunk_id,
            chunk_metadata,
            base_api_url=base_api_url,
        )

    results = await run_doc_extractors_concurrently(
        chunk_items=doc_items,
        job_id=job_id,
        extractor=extractor_with_metadata,
        logger_scope="Digester:ConnectivityEndpoint",
    )

    for candidates, has_relevant_data, chunk_id in results:
        chunk_id_str = str(chunk_id)
        doc_id = chunk_id_to_doc_id.get(chunk_id_str)

        candidates_for_chunk = candidates if isinstance(candidates, list) else []
        all_candidates.extend(candidates_for_chunk)

        if not has_relevant_data:
            continue

        if not doc_id:
            logger.warning(
                "[Digester:ConnectivityEndpoint] Missing docId for chunk %s, skipping relevant chunk mapping",
                chunk_id_str,
            )
            continue

        chunk_ref = {"doc_id": doc_id, "chunk_id": chunk_id_str}
        all_relevant_chunks.append(chunk_ref)
        for candidate in candidates_for_chunk:
            key = normalize_endpoint_key(candidate.path, candidate.method)
            if key:
                endpoint_chunk_pairs.setdefault(key, set()).add((doc_id, chunk_id_str))

    await update_job_progress(
        job_id,
        stage=JobStage.deduplication,
        message="Ranking connectivity endpoint candidates",
    )
    response = await merge_and_rank_connectivity_endpoint_candidates(all_candidates, endpoint_chunk_pairs, job_id)

    await update_job_progress(
        job_id,
        stage=JobStage.schema_ready,
        message="Connectivity endpoint extraction complete",
    )
    return {
        "result": response.model_dump(by_alias=True, mode="json"),
        "relevantDocumentations": all_relevant_chunks,
    }


async def extract_connectivity_endpoint(
    doc_items: List[dict],
    session_id: UUID,
    job_id: UUID,
    base_api_url: str = "",
) -> Dict[str, Any]:
    """
    Extract and rank endpoints suitable for testing connectivity between midPoint connector generator and the target app.
    Retries with broader documentation criteria when endpoint-focused chunks produce no candidates.
    """
    result = await _extract_connectivity_endpoint_from_doc_items(doc_items, job_id, base_api_url)
    if _connectivity_endpoint_result_has_item(result):
        return result

    logger.info(
        "[Digester:ConnectivityEndpoint] Primary documentation produced no connectivity endpoint for session %s; "
        "retrying with fallback criteria",
        session_id,
    )
    await update_job_progress(
        job_id,
        stage="chunking",
        message="No connectivity endpoint found in primary chunks; retrying with broader filter",
    )

    fallback_doc_items = await filter_documentation_items(CONNECTIVITY_ENDPOINT_FALLBACK_CRITERIA, session_id)
    if not fallback_doc_items:
        logger.info(
            "[Digester:ConnectivityEndpoint] Fallback criteria matched no documentation for session %s",
            session_id,
        )
        return result

    primary_chunk_ids = {str(item.get("chunkId") or "").strip() for item in doc_items if item.get("chunkId")}
    fallback_doc_items = exclude_doc_items_by_chunk_id(fallback_doc_items, primary_chunk_ids)
    if not fallback_doc_items:
        logger.info(
            "[Digester:ConnectivityEndpoint] Fallback criteria produced no new chunks for session %s",
            session_id,
        )
        return result

    fallback_result = await _extract_connectivity_endpoint_from_doc_items(fallback_doc_items, job_id, base_api_url)
    if _connectivity_endpoint_result_has_item(fallback_result):
        return fallback_result

    return result


async def extract_attributes(
    doc_items: List[dict],
    object_class: str,
    session_id: UUID,
    relevant_chunks: List[Dict[str, Any]],
    job_id: UUID,
    api_type_override: ApiType | None = None,
) -> Dict[str, Any]:
    """
    Extract attributes from only the relevant chunks of documentation and update the specific object class
    in objectClassesOutput with the extracted attributes.

    The extraction protocol (REST/SCIM/SQL) is taken from ``api_type_override`` when provided,
    otherwise it is derived from the apiType stored in the session ``infoMetadata``.

    Args:
        doc_items: Full documentation items
        object_class: Name of the object class
        session_id: Session ID
        relevant_chunks: List of {doc_id, chunk_id} dicts indicating which chunks to process
        job_id: Job ID for progress tracking
        api_type_override: Explicit protocol override; falls back to detected apiType when None
    """
    # TODO: Refactor this function
    protocol = await resolve_effective_api_type(session_id, api_type_override)
    if protocol == ApiType.SQL:
        result = await extract_sql_attributes(doc_items, object_class, job_id)
        try:
            attributes_dict = extract_attributes_from_result(result)
            logger.info("[Digester:Attributes] Extracted %d SQL attributes for %s", len(attributes_dict), object_class)
            updated = await update_object_class_field_in_session(
                session_id=session_id,
                object_class=object_class,
                field_name="attributes",
                field_value=attributes_dict,
            )
            if not updated:
                logger.warning("[Digester:Attributes] Failed to update objectClassesOutput for %s", object_class)
        except Exception:
            logger.exception(
                "[Digester:Attributes] Exception while updating object class with SQL attributes for %s",
                object_class,
            )
        return result

    is_scim = protocol == ApiType.SCIM

    if not doc_items:
        if is_scim:
            logger.info(
                "[Digester:Attributes] No documentation provided for SCIM %s; using schema heuristics",
                object_class,
            )
            selected_content: List[str] = []
            chunk_ids: List[str] = []
            chunk_metadata_map: Dict[str, Any] = {}
            chunk_id_to_doc_id: Dict[str, str] = {}
        else:
            logger.warning(f"[Digester:Attributes] No documentation provided for {object_class}")
            return {"result": {"attributes": {}}, "relevantDocumentations": []}
    elif not relevant_chunks:
        if is_scim:
            logger.info(
                "[Digester:Attributes] No relevant chunks provided for SCIM %s; using schema heuristics",
                object_class,
            )
            selected_content = []
            chunk_ids = []
            chunk_metadata_map = build_doc_metadata_map(doc_items)
            chunk_id_to_doc_id = build_chunk_id_to_doc_id(doc_items)
        else:
            logger.warning(f"[Digester:Attributes] No relevant chunks provided for {object_class}")
            return {"result": {"attributes": {}}, "relevantDocumentations": []}
    else:
        selected_content, chunk_ids = select_doc_chunks(doc_items, relevant_chunks, "Digester:Attributes")

        if not selected_content:
            if is_scim:
                logger.info(
                    "[Digester:Attributes] No selected documentation chunks for SCIM %s; using schema heuristics",
                    object_class,
                )
                selected_content = []
                chunk_ids = []
            else:
                logger.warning(f"[Digester:Attributes] No relevant chunks found for {object_class}")
                return {"result": {"attributes": {}}, "relevantDocumentations": []}

        chunk_metadata_map = build_doc_metadata_map(doc_items)
        chunk_id_to_doc_id = build_chunk_id_to_doc_id(doc_items)

    if is_scim:
        result = await extract_scim_attributes(
            selected_content,
            object_class,
            job_id,
            chunk_ids,
            chunk_metadata_map,
            chunk_id_to_doc_id,
        )
    else:
        result = await _extract_rest_attributes(
            selected_content,
            object_class,
            job_id,
            chunk_ids,
            chunk_metadata_map,
            chunk_id_to_doc_id,
        )

    try:
        attributes_dict = extract_attributes_from_result(result)
        logger.info("[Digester:Attributes] Extracted %d attributes for %s", len(attributes_dict), object_class)

        if len(attributes_dict) == 0:
            logger.warning(
                f"[Digester:Attributes] No attributes extracted for {object_class} from relevant chunks, retrying with default criteria"
            )
            # Retry with default criteria
            result_retry = await _retry_attributes_with_default_criteria(
                doc_items,
                object_class,
                session_id,
                job_id,
                relevant_chunks,
                chunk_metadata_map,
                chunk_id_to_doc_id,
                is_scim=is_scim,
            )
            attributes_dict_retry = extract_attributes_from_result(result_retry)

            if attributes_dict_retry and result_retry is not None:
                logger.info(
                    "[Digester:Attributes] Extracted %d attributes for %s on retry with default criteria",
                    len(attributes_dict_retry),
                    object_class,
                )
                attributes_dict = attributes_dict_retry
                result = result_retry

        updated = await update_object_class_field_in_session(
            session_id=session_id,
            object_class=object_class,
            field_name="attributes",
            field_value=attributes_dict,
        )
        if not updated:
            logger.warning("[Digester:Attributes] Failed to update objectClassesOutput for %s", object_class)

    except Exception:
        logger.exception(
            "[Digester:Attributes] Exception while updating object class with attributes for %s", object_class
        )

    return result


async def extract_endpoints(
    doc_items: List[dict],
    object_class: str,
    session_id: UUID,
    relevant_chunks: List[Dict[str, Any]],
    job_id: UUID,
    base_api_url: str = "",
    api_type_override: ApiType | None = None,
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
    """

    protocol = await resolve_effective_api_type(session_id, api_type_override)
    if protocol == ApiType.SQL:
        result = await extract_sql_tables(doc_items, object_class, job_id)
        try:
            tables_list = extract_endpoints_from_result(result)
            logger.info("[Digester:Endpoints] Selected %d SQL tables for %s", len(tables_list), object_class)
            updated = await update_object_class_field_in_session(
                session_id=session_id,
                object_class=object_class,
                field_name="endpoints",
                field_value=tables_list,
            )
            if not updated:
                logger.warning("[Digester:Endpoints] Failed to update objectClassesOutput for %s", object_class)
        except Exception:
            logger.exception("[Digester:Endpoints] Failed to update object class with SQL tables for %s", object_class)
        return result

    is_scim = protocol == ApiType.SCIM

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
    except Exception:
        logger.exception("[Digester:Endpoints] Failed to update object class with endpoints for %s", object_class)

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
