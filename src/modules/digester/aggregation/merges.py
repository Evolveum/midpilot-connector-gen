# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
import logging
from typing import Any, Callable, Dict, List, Optional, cast
from uuid import UUID

from langchain_core.runnables.config import RunnableConfig

from src.common.enums import ApiType, JobStage
from src.common.jobs import append_job_error, update_job_progress
from src.common.langfuse import langfuse_handler
from src.config import config
from src.modules.digester.enums import EndpointMethod, EndpointType
from src.modules.digester.extraction.llm_execution import invoke_llm
from src.modules.digester.extraction.sequences import extract_sequence
from src.modules.digester.schemas import (
    ApiTypeResponse,
    AttributeDedupResponse,
    AttributeProcessingInfo,
    BaseAPIEndpoint,
    DiscoveryAttribute,
    DocProcessingSequenceItem,
    DocSequenceItem,
    ExtendedObjectClass,
    ExtractedEndpointInfo,
    InfoMetadata,
    InfoMetadataExtraction,
    InfoResponse,
)

logger = logging.getLogger(__name__)


def merge_relations_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Merge relations results from multiple documents.

    Combines all relations from different documents and deduplicates them based on:
    - subject
    - subjectAttribute
    - object
    - objectAttribute
    """
    # Get all relations from all results
    all_relations = []
    for result in results:
        if isinstance(result, dict) and "relations" in result:
            all_relations.extend(result["relations"])

    # Deduplicate relations based on the specified parameters
    seen = set()
    unique_relations = []

    for rel in all_relations:
        # Create a unique key based on the specified parameters
        key = (
            rel.get("subject", ""),
            rel.get("subjectAttribute", ""),
            rel.get("object", ""),
            rel.get("objectAttribute", ""),
        )

        # Only add if we haven't seen this combination before
        if key not in seen:
            seen.add(key)
            unique_relations.append(rel)

    return {"relations": unique_relations}


def merge_object_classes(
    all_object_classes: List[ExtendedObjectClass],
    class_to_chunks: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> List[ExtendedObjectClass]:
    """
    Deduplicate and merge object classes across documents.

    - Merges class metadata (superclass/abstract/embedded/description).
    - Removes duplicates that differ only by whitespace.
    """
    by_name: Dict[str, ExtendedObjectClass] = {}

    for obj_class in all_object_classes:
        if not obj_class or not obj_class.name:
            continue
        key = obj_class.name.strip().lower()
        if key not in by_name:
            by_name[key] = obj_class
            continue

        current = by_name[key]
        # Prefer non-empty superclass, keep original if new is empty
        if obj_class.superclass and not current.superclass:
            current.superclass = obj_class.superclass
        # OR booleans (any evidence of True wins)
        current.abstract = current.abstract or obj_class.abstract
        current.embedded = current.embedded or obj_class.embedded
        # Prefer longer, non-empty description
        if obj_class.description and len(obj_class.description) > len(current.description or ""):
            current.description = obj_class.description

    # Remove duplicates with whitespace-only differences (preferring no-space versions)
    for key in list(by_name.keys()):
        key_no_space = key.replace(" ", "")
        if key != key_no_space and key_no_space in by_name:
            by_name.pop(key)

    return list(by_name.values())


async def merge_attribute_candidates(
    object_class: str,
    attribute_objects: List[DiscoveryAttribute] | List[AttributeProcessingInfo],
    job_id: UUID,
    build_dedup_chain: Callable[[], Any],
    chunk_id_doc_id_map: Optional[Dict[str, str]] = None,
) -> List[AttributeProcessingInfo]:
    """
    Merge attribute candidates extracted from multiple chunks and deduplicate in two passes:
    1) heuristic exact-name deduplication
    2) LLM deduplication/cleanup

    Args:
    - object_class: Name of the object class these attributes belong to
    - attribute_objects: List of DiscoveryAttribute or AttributeProcessingInfo objects extracted from different chunks
    - job_id: UUID of current job
    - dedup_chain: Callable that takes a dict with object_class and attributes_list (JSON string) and returns an AttributeDedupResponse with duplicates and to_be_deleted lists

    Returns:
    - List of merged, deduplicated and cleaned AttributeProcessingInfo objects

    """

    logger.info(
        "[Digester:Attributes] Original names of candidates for %s: %s",
        object_class,
        [attr.name for attr in attribute_objects],
    )

    await update_job_progress(
        job_id,
        stage=JobStage.deduplication,
        message=f"Deduplicating attributes for {object_class}",
    )

    def _sequence_key(seq: DocProcessingSequenceItem) -> tuple[str, str, str]:
        return (seq.chunk_id, seq.start_sequence, seq.end_sequence)

    def _merge_relevant_sequences(target: AttributeProcessingInfo, source: AttributeProcessingInfo) -> None:
        existing_keys = {_sequence_key(seq) for seq in target.relevant_sequences}
        for seq in source.relevant_sequences:
            key = _sequence_key(seq)
            if key not in existing_keys:
                target.relevant_sequences.append(seq)
                existing_keys.add(key)

    def _merge_metadata(target: AttributeProcessingInfo, source: AttributeProcessingInfo) -> None:
        if source.type and not target.type:
            target.type = source.type
        if source.format and not target.format:
            target.format = source.format
        if source.description and len(source.description) > len(target.description or ""):
            target.description = source.description

        if target.mandatory is None and source.mandatory is not None:
            target.mandatory = source.mandatory
        if target.updatable is None and source.updatable is not None:
            target.updatable = source.updatable
        if target.creatable is None and source.creatable is not None:
            target.creatable = source.creatable
        if target.readable is None and source.readable is not None:
            target.readable = source.readable
        if target.multivalue is None and source.multivalue is not None:
            target.multivalue = source.multivalue
        if target.returnedByDefault is None and source.returnedByDefault is not None:
            target.returnedByDefault = source.returnedByDefault

    def _merge_relevant_documentations(target: AttributeProcessingInfo, source: AttributeProcessingInfo) -> None:
        for doc in source.relevant_documentations:
            if doc not in target.relevant_documentations:
                target.relevant_documentations.append(doc)

    async def _to_processing_info(attr: DiscoveryAttribute | AttributeProcessingInfo) -> AttributeProcessingInfo | None:
        if isinstance(attr, AttributeProcessingInfo):
            return attr

        relevant_sequences: List[DocProcessingSequenceItem] = []
        for raw_seq in attr.relevant_sequences:
            seq = DocSequenceItem.model_validate(raw_seq.model_dump(by_alias=True))

            relevant_sequences.append(
                DocProcessingSequenceItem(
                    chunk_id=seq.chunk_id,
                    start_sequence=seq.start_sequence,
                    end_sequence=seq.end_sequence,
                    text=await extract_sequence(
                        seq.chunk_id,
                        seq.start_sequence,
                        seq.end_sequence,
                        enable_marker_blending=True,
                        logger_prefix="[Digester:Attributes] [Merge] ",
                    ),
                )
            )

        if relevant_sequences:
            first_chunk_id = relevant_sequences[0].chunk_id
            first_doc_id = (chunk_id_doc_id_map.get(first_chunk_id) or "unknown") if chunk_id_doc_id_map else "unknown"

            return AttributeProcessingInfo(
                name=attr.name,
                type=getattr(attr, "type", None),
                format=getattr(attr, "format", None),
                description=attr.description,
                mandatory=None,
                updatable=None,
                creatable=None,
                readable=None,
                multivalue=None,
                returnedByDefault=None,
                relevant_sequences=relevant_sequences,
                relevant_documentations=[
                    {"chunk_id": first_chunk_id, "doc_id": first_doc_id},
                ],
            )
        else:
            logger.warning("[Digester:Attributes] Attribute %s has no relevant sequences; deleting", attr.name)
            return None

    if not attribute_objects:
        return []

    await update_job_progress(
        job_id,
        stage=JobStage.deduplication,
        message=f"Deduplicating attributes for {object_class}",
    )

    seen: Dict[str, AttributeProcessingInfo] = {}
    for attr in attribute_objects:
        if not attr or not attr.name:
            continue

        key = attr.name.strip().lower()
        if not key:
            continue

        item = await _to_processing_info(attr)

        if item is None:
            logger.warning("[Digester:Attributes] Skipping attribute with empty name after processing: %s", attr)
            continue

        if key not in seen:
            seen[key] = item
            continue

        current = seen[key]
        _merge_relevant_sequences(current, item)
        _merge_metadata(current, item)
        _merge_relevant_documentations(current, item)

    merged = list(seen.values())
    logger.info("[Digester:Attributes] Heuristic merge complete. Unique count: %d", len(merged))
    # TODO: DELETE
    logger.info(
        "[Digester:Attributes] Names of candidates after heuristic merge for %s: %s",
        object_class,
        [attr.name for attr in merged],
    )

    if len(merged) <= 1:
        await update_job_progress(
            job_id,
            stage=JobStage.deduplication_finished,
            message="Attribute deduplication finished",
        )
        return merged

    try:
        dedup_chain = build_dedup_chain()
        result = cast(
            AttributeDedupResponse,
            await invoke_llm(
                dedup_chain,
                {
                    "object_class": object_class,
                    "attributes_list": json.dumps([item.model_dump() for item in merged]),
                },
                config=RunnableConfig(callbacks=[langfuse_handler]),
            ),
        )

        # TODO: DELETE
        logger.info("[Digester:Attributes] LLM deduplication result for %s: %s", object_class, result)

        if not result:
            logger.warning("[Digester:Attributes] Deduplication LLM returned empty result; keeping heuristic output.")
            await update_job_progress(
                job_id,
                stage=JobStage.deduplication_finished,
                message="Attribute deduplication finished (LLM empty)",
            )
            return merged

        by_name: Dict[str, AttributeProcessingInfo] = {item.name.strip().lower(): item for item in merged}
        mark_for_deletion: set[str] = set()

        for keep_name, delete_name in result.duplicates or []:
            keep_key = keep_name.strip().lower()
            delete_key = delete_name.strip().lower()

            if keep_key == delete_key:
                continue

            keep_item = by_name.get(keep_key)
            delete_item = by_name.get(delete_key)

            if keep_item and delete_item:
                _merge_relevant_sequences(keep_item, delete_item)
                _merge_metadata(keep_item, delete_item)
                mark_for_deletion.add(delete_key)
            else:
                logger.warning(
                    "[Digester:Attributes] Could not resolve dedup pair keep=%s delete=%s",
                    keep_name,
                    delete_name,
                )

        for delete_name in result.to_be_deleted or []:
            key = delete_name.strip().lower()
            if key in by_name:
                mark_for_deletion.add(key)

        final_list = [item for item in merged if item.name.strip().lower() not in mark_for_deletion]

        await update_job_progress(
            job_id,
            stage=JobStage.deduplication_finished,
            message="Attribute deduplication finished",
        )
        logger.info("[Digester:Attributes] LLM dedup complete. Final count: %d", len(final_list))
        return final_list

    except Exception as exc:
        logger.error("[Digester:Attributes] Deduplication LLM call failed: %s", exc)
        await update_job_progress(
            job_id,
            stage=JobStage.deduplication_failed,
            message=f"Attribute deduplication failed: {exc}",
        )
        append_job_error(job_id, f"[Digester:Attributes] Deduplication LLM call failed: {exc}")
        return merged


async def merge_endpoint_candidates(
    extracted_endpoints: List[ExtractedEndpointInfo], object_class: str, job_id: UUID
) -> List[Dict[str, Any]]:
    """
    Merge and deduplicate endpoint candidates extracted from multiple chunks.

    :param extracted_endpoints: List of endpoints extracted from different chunks
    :param object_class: Name of the object class for logging
    :param job_id: Job ID for progress updates
    :return: List of merged endpoint dictionaries
    """

    # HTTP method ordering for consistent sorting
    _METHOD_ORDER: Dict[EndpointMethod, int] = {
        EndpointMethod.GET: 0,
        EndpointMethod.POST: 1,
        EndpointMethod.PUT: 2,
        EndpointMethod.PATCH: 3,
        EndpointMethod.DELETE: 4,
    }

    def _endpoint_key(ep: ExtractedEndpointInfo) -> tuple[str, EndpointMethod]:
        return (ep.path.strip(), ep.method)

    by_key: Dict[tuple[str, EndpointMethod], ExtractedEndpointInfo] = {}

    for ep in extracted_endpoints:
        if not ep.path:
            continue

        key = _endpoint_key(ep)

        if key not in by_key:
            by_key[key] = ep
            continue

        current = by_key[key]

        # Prefer longer, non-empty description
        if (ep.description or "") and len(ep.description) > len(current.description or ""):
            current.description = ep.description

        # Prefer non-empty content types
        if not current.request_content_type and ep.request_content_type:
            current.request_content_type = ep.request_content_type
        if not current.response_content_type and ep.response_content_type:
            current.response_content_type = ep.response_content_type

        # Merge suggested_use (unique, preserve order)
        if ep.suggested_use:
            existing = list(current.suggested_use or [])
            for su in ep.suggested_use:
                if su not in existing:
                    existing.append(su)
            current.suggested_use = existing

    merged = list(by_key.values())

    # Sort by path, then by common HTTP method order
    merged.sort(key=lambda e: (e.path, _METHOD_ORDER[e.method], e.method))

    # Convert to dicts for JSON serialization
    merged_dicts = [ep.model_dump(by_alias=True, exclude={"relevant_documentations"}) for ep in merged]

    logger.info("[Digester:Endpoints] Merged %d endpoints for %s", len(merged_dicts), object_class)

    await update_job_progress(
        job_id,
        stage=JobStage.finished,
        message="complete",
    )

    return merged_dicts


def is_empty_info_result_payload(payload: Dict[str, Any]) -> bool:
    """Detect if InfoResponse-like payload has no extracted metadata."""
    info = (payload or {}).get("infoMetadata")
    if info is None:
        return True
    if not isinstance(info, dict):
        return True

    return not bool(
        str(info.get("name") or "").strip()
        or str(info.get("applicationVersion") or "").strip()
        or str(info.get("apiVersion") or "").strip()
        or info.get("apiType")
        or info.get("baseApiEndpoint")
        or str(info.get("databaseName") or "").strip()
    )


def _empty_info_metadata_payload() -> Dict[str, Any]:
    """Standard empty metadata payload for API responses."""
    return cast(Dict[str, Any], InfoResponse(info_metadata=None).model_dump(by_alias=True))


def merge_api_type(
    api_type_candidates: List[ApiTypeResponse],
    total_items: int,
) -> List[ApiType]:
    """
    Merge per-chunk apiType candidates into a final list using the same frequency
    threshold heuristic as the rest of the info metadata merge:
    - count how often each supported type occurs across processed documents,
    - keep only types that occur frequently enough (above the uncertainty threshold).
    """
    if total_items <= 0:
        logger.info("[Digester:ApiType] Merge skipped: total_items=%s", total_items)
        return []

    threshold = total_items * config.digester.info_metadata_uncertainty_threshold

    api_type_distribution: Dict[ApiType, int] = {}
    for candidate in api_type_candidates:
        for api_type in candidate.api_type or []:
            normalized_type = str(api_type).strip().lower()
            if normalized_type in {ApiType.REST.value, ApiType.SCIM.value, ApiType.SQL.value}:
                canonical_type = ApiType(normalized_type)
                api_type_distribution[canonical_type] = api_type_distribution.get(canonical_type, 0) + 1

    found_api_types = sorted(
        (api_type for api_type, count in api_type_distribution.items() if count > threshold),
        key=lambda api_type: api_type.value,
    )

    logger.info(
        "[Digester:ApiType] Distribution: %s threshold_count=%s selected=%s",
        api_type_distribution,
        threshold,
        found_api_types,
    )
    return found_api_types


def merge_info_metadata(
    info_candidates: List[InfoMetadataExtraction],
    total_items: int,
    api_types: List[ApiType],
) -> Dict[str, Any]:
    """
    Merge per-document InfoMetadata candidates into a single payload using frequency heuristics.

    This mirrors the threshold-based strategy previously used for processor metadata:
    - keep values that occur frequently enough across all processed documents
    - ignore sparse/noisy values
    """
    if total_items <= 0:
        logger.info("[Digester:InfoMetadata] Heuristic merge skipped: total_items=%s", total_items)
        return _empty_info_metadata_payload()

    threshold = total_items * config.digester.info_metadata_uncertainty_threshold

    name_distribution: Dict[str, int] = {}
    app_version_distribution: Dict[str, int] = {}
    api_version_distribution: Dict[str, int] = {}
    base_api_endpoints_url_distribution: Dict[str, int] = {}
    base_api_endpoints_type_distribution: Dict[tuple[str, EndpointType], int] = {}
    database_name_distribution: Dict[str, int] = {}

    for info in info_candidates:
        name = (info.name or "").strip()
        if name:
            name_distribution[name] = name_distribution.get(name, 0) + 1

        application_version = (info.application_version or "").strip()
        if application_version:
            app_version_distribution[application_version] = app_version_distribution.get(application_version, 0) + 1

        api_version = (info.api_version or "").strip()
        if api_version:
            api_version_distribution[api_version] = api_version_distribution.get(api_version, 0) + 1

        for endpoint in info.base_api_endpoint or []:
            uri = (endpoint.uri or "").strip().lower()
            endpoint_type: EndpointType = endpoint.type
            if not uri:
                continue
            base_api_endpoints_url_distribution[uri] = base_api_endpoints_url_distribution.get(uri, 0) + 1
            key = (uri, endpoint_type)
            base_api_endpoints_type_distribution[key] = base_api_endpoints_type_distribution.get(key, 0) + 1

        database_name = (info.database_name or "").strip()
        if database_name:
            database_name_distribution[database_name] = database_name_distribution.get(database_name, 0) + 1

    found_name = ""
    if name_distribution:
        candidate_name = max(name_distribution.keys(), key=lambda value: name_distribution[value])
        if name_distribution[candidate_name] > threshold:
            found_name = candidate_name

    found_application_version = ""
    if app_version_distribution:
        candidate_version = max(app_version_distribution.keys(), key=lambda value: app_version_distribution[value])
        if app_version_distribution[candidate_version] > threshold:
            found_application_version = candidate_version

    found_api_version = ""
    if api_version_distribution:
        candidate_api_version = max(api_version_distribution.keys(), key=lambda value: api_version_distribution[value])
        if api_version_distribution[candidate_api_version] > threshold:
            found_api_version = candidate_api_version

    found_api_types: List[ApiType] = sorted(api_types, key=lambda api_type: api_type.value)

    found_base_api_endpoints: List[BaseAPIEndpoint] = []
    for uri, count in base_api_endpoints_url_distribution.items():
        if count <= threshold:
            continue

        type_distribution: Dict[EndpointType, int] = {
            endpoint_type: base_api_endpoints_type_distribution.get((uri, endpoint_type), 0)
            for endpoint_type in (EndpointType.CONSTANT, EndpointType.DYNAMIC, EndpointType.UNKNOWN)
        }
        top_count = max(type_distribution.values(), default=0)
        top_types = [
            endpoint_type for endpoint_type, type_count in type_distribution.items() if type_count == top_count
        ]

        selected_endpoint_type: EndpointType = (
            top_types[0] if top_count > 0 and len(top_types) == 1 else EndpointType.UNKNOWN
        )
        found_base_api_endpoints.append(BaseAPIEndpoint(uri=uri, type=selected_endpoint_type))

    found_database_name = ""
    if database_name_distribution:
        candidate_database_name = max(
            database_name_distribution.keys(), key=lambda value: database_name_distribution[value]
        )
        if database_name_distribution[candidate_database_name] > threshold:
            found_database_name = candidate_database_name

    if ApiType.SQL not in found_api_types:
        found_database_name = ""

    logger.info(
        "[Digester:InfoMetadata] Heuristic threshold: total_docs=%s threshold_count=%s",
        total_items,
        threshold,
    )
    logger.info("[Digester:InfoMetadata] Name distribution: %s", name_distribution)
    logger.info("[Digester:InfoMetadata] Application version distribution: %s", app_version_distribution)
    logger.info("[Digester:InfoMetadata] API version distribution: %s", api_version_distribution)
    logger.info("[Digester:InfoMetadata] Base API endpoint URI distribution: %s", base_api_endpoints_url_distribution)
    logger.info(
        "[Digester:InfoMetadata] Base API endpoint (URI, type) distribution: %s",
        base_api_endpoints_type_distribution,
    )
    logger.info("[Digester:InfoMetadata] Database name distribution: %s", database_name_distribution)
    logger.info(
        "[Digester:InfoMetadata] Heuristic selected values: name=%r applicationVersion=%r apiVersion=%r "
        "apiType=%s baseApiEndpoint=%s databaseName=%r",
        found_name,
        found_application_version,
        found_api_version,
        found_api_types,
        [endpoint.model_dump() for endpoint in found_base_api_endpoints],
        found_database_name,
    )

    merged_response = InfoResponse(
        info_metadata=InfoMetadata(
            name=found_name,
            application_version=found_application_version,
            api_version=found_api_version,
            api_type=found_api_types,
            base_api_endpoint=found_base_api_endpoints,
            database_name=found_database_name,
        )
    )
    merged_payload = cast(Dict[str, Any], merged_response.model_dump(by_alias=True))

    if is_empty_info_result_payload(merged_payload):
        logger.info("[Digester:InfoMetadata] Heuristic result is empty -> returning infoMetadata=null")
        return _empty_info_metadata_payload()

    logger.info("[Digester:InfoMetadata] Heuristic merge produced non-empty infoMetadata")
    return merged_payload
