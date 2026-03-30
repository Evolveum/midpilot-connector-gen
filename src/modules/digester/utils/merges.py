# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
import logging
from collections import defaultdict
from typing import Any, Callable, Dict, List, Literal, Optional, cast
from uuid import UUID

from langchain_core.runnables.config import RunnableConfig

from src.common.enums import JobStage
from src.common.jobs import update_job_progress
from src.common.langfuse import langfuse_handler
from src.config import config
from src.modules.digester.schema import (
    AttributeResponse,
    BaseAPIEndpoint,
    EndpointInfo,
    EndpointMethod,
    InfoMetadata,
    InfoResponse,
    ObjectClass,
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
    all_object_classes: List[ObjectClass],
    class_to_chunks: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> List[ObjectClass]:
    """
    Deduplicate and merge object classes across documents.

    - Merges class metadata (superclass/abstract/embedded/description).
    - Merges relevant chunks by (doc_id, chunk_id) when provided.
    - Removes duplicates that differ only by whitespace.
    """
    by_name: Dict[str, ObjectClass] = {}

    for obj_class in all_object_classes:
        if not obj_class or not obj_class.name:
            continue
        key = obj_class.name.strip().lower()
        if key not in by_name:
            # If we have chunk information for this class, set it
            if class_to_chunks and key in class_to_chunks:
                unique_chunks: List[Dict[str, Any]] = []
                seen: set[tuple[str, str]] = set()
                for chunk in class_to_chunks[key]:
                    doc_id = str(chunk.get("doc_id", "")).strip()
                    chunk_id = str(chunk.get("chunk_id", "")).strip()
                    if not doc_id or not chunk_id:
                        continue

                    pair = (doc_id, chunk_id)
                    if pair in seen:
                        continue

                    seen.add(pair)
                    unique_chunks.append({"doc_id": doc_id, "chunk_id": chunk_id})

                obj_class.relevant_documentations = sorted(unique_chunks, key=lambda x: (x["doc_id"], x["chunk_id"]))
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
        # Merge relevant chunks if available
        if class_to_chunks and key in class_to_chunks:
            current_chunk_pairs = {
                (str(chunk.get("doc_id", "")).strip(), str(chunk.get("chunk_id", "")).strip())
                for chunk in (current.relevant_documentations or [])
                if str(chunk.get("doc_id", "")).strip() and str(chunk.get("chunk_id", "")).strip()
            }

            for chunk in class_to_chunks[key]:
                doc_id = str(chunk.get("doc_id", "")).strip()
                chunk_id = str(chunk.get("chunk_id", "")).strip()
                if not doc_id or not chunk_id:
                    continue
                current_chunk_pairs.add((doc_id, chunk_id))

            current.relevant_documentations = [
                {"doc_id": doc_id, "chunk_id": chunk_id}
                for doc_id, chunk_id in sorted(current_chunk_pairs, key=lambda pair: (pair[0], pair[1]))
            ]
    # Remove duplicates with whitespace-only differences (preferring no-space versions)
    for key in list(by_name.keys()):
        key_no_space = key.replace(" ", "")
        if key != key_no_space and key_no_space in by_name:
            by_name.pop(key)

    return list(by_name.values())


async def merge_attribute_candidates(
    *,
    object_class: str,
    per_chunk: List[Dict[str, Dict[str, Any]]],
    job_id: UUID,
    build_dedupe_chain: Callable[[], Any],
) -> Dict[str, Dict[str, Any]]:
    """
    Merge attribute candidates extracted from multiple chunks and deduplicate via LLM when needed.
    """

    await update_job_progress(
        job_id,
        stage="merging",
        message=f"Merging and deduplicating attributes for {object_class}",
    )

    candidates: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for partial in per_chunk:
        if not partial:
            continue
        for attr_name, attr_info in partial.items():
            info_copy = dict(attr_info)
            info_copy.setdefault("name", attr_name)
            candidates[attr_name].append({"info": info_copy})

    if not candidates:
        return {}

    if not any(len(v) > 1 for v in candidates.values()):
        return {name: infos[0]["info"] for name, infos in candidates.items()}

    await update_job_progress(
        job_id,
        stage=JobStage.resolving_duplicates,
        message=f"Resolving duplicate attributes for {object_class}",
    )

    dedupe_chain = build_dedupe_chain()
    payload = json.dumps(candidates, ensure_ascii=False)

    try:
        result = await dedupe_chain.ainvoke(
            {
                "object_class": object_class,
                "candidates_json": payload,
                "guaranteed_candidates_per_name": True,
            },
            config=RunnableConfig(callbacks=[langfuse_handler]),
        )

        if isinstance(result, AttributeResponse):
            parsed = result
        else:
            content = getattr(result, "content", None)
            parsed = AttributeResponse.model_validate(json.loads(content)) if content else AttributeResponse()

        return {
            name: info.model_dump(exclude={"relevant_documentations", "scimAttribute"})
            for name, info in parsed.attributes.items()
        }

    except Exception as exc:
        logger.error("[Digester:Attributes] Dedupe failed: %s", exc)
        fallback: Dict[str, Dict[str, Any]] = {}
        object_class_lower = object_class.lower()
        for attr_name, attr_list in candidates.items():
            best = max(
                attr_list,
                key=lambda c: int(object_class_lower in (c["info"].get("description", "").lower())),
            )
            fallback[attr_name] = cast(Dict[str, Any], best["info"])
        return fallback


async def merge_endpoint_candidates(
    extracted_endpoints: List[EndpointInfo], object_class: str, job_id: UUID
) -> List[Dict[str, Any]]:
    """
    Merge and deduplicate endpoint candidates extracted from multiple chunks.

    :param extracted_endpoints: List of endpoints extracted from different chunks
    :param object_class: Name of the object class for logging
    :param job_id: Job ID for progress updates
    :return: List of merged endpoint dictionaries
    """

    # HTTP method ordering for consistent sorting
    _METHOD_ORDER: Dict[EndpointMethod, int] = {"GET": 0, "POST": 1, "PUT": 2, "PATCH": 3, "DELETE": 4}

    def _endpoint_key(ep: EndpointInfo) -> tuple[str, EndpointMethod]:
        return (ep.path.strip(), ep.method)

    by_key: Dict[tuple[str, EndpointMethod], EndpointInfo] = {}

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
    )


def _empty_info_metadata_payload() -> Dict[str, Any]:
    """Standard empty metadata payload for API responses."""
    return cast(Dict[str, Any], InfoResponse(info_metadata=None).model_dump(by_alias=True))


def merge_info_metadata(
    info_candidates: List[InfoMetadata],
    total_items: int,
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
    api_type_distribution: Dict[str, int] = {}
    base_api_endpoints_url_distribution: Dict[str, int] = {}
    base_api_endpoints_type_distribution: Dict[tuple[str, str], int] = {}

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

        for api_type in info.api_type or []:
            normalized_type = str(api_type).upper().strip()
            if normalized_type in {"REST", "SCIM"}:
                api_type_distribution[normalized_type] = api_type_distribution.get(normalized_type, 0) + 1

        for endpoint in info.base_api_endpoint or []:
            uri = (endpoint.uri or "").strip().lower()
            endpoint_type: Literal["constant", "dynamic"] = endpoint.type
            if not uri:
                continue
            base_api_endpoints_url_distribution[uri] = base_api_endpoints_url_distribution.get(uri, 0) + 1
            key = (uri, endpoint_type)
            base_api_endpoints_type_distribution[key] = base_api_endpoints_type_distribution.get(key, 0) + 1

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

    found_api_types: List[Literal["REST", "SCIM"]] = [
        cast(Literal["REST", "SCIM"], api_type)
        for api_type, count in api_type_distribution.items()
        if count > threshold
    ]
    found_api_types = sorted(found_api_types)

    found_base_api_endpoints: List[BaseAPIEndpoint] = []
    for uri, count in base_api_endpoints_url_distribution.items():
        if count <= threshold:
            continue

        constant_count = base_api_endpoints_type_distribution.get((uri, "constant"), 0)
        dynamic_count = base_api_endpoints_type_distribution.get((uri, "dynamic"), 0)
        selected_endpoint_type: Literal["constant", "dynamic"] = (
            "constant" if constant_count >= dynamic_count else "dynamic"
        )
        found_base_api_endpoints.append(BaseAPIEndpoint(uri=uri, type=selected_endpoint_type))

    logger.info(
        "[Digester:InfoMetadata] Heuristic threshold: total_docs=%s threshold_count=%s",
        total_items,
        threshold,
    )
    logger.info("[Digester:InfoMetadata] Name distribution: %s", name_distribution)
    logger.info("[Digester:InfoMetadata] Application version distribution: %s", app_version_distribution)
    logger.info("[Digester:InfoMetadata] API version distribution: %s", api_version_distribution)
    logger.info("[Digester:InfoMetadata] API type distribution: %s", api_type_distribution)
    logger.info("[Digester:InfoMetadata] Base API endpoint URI distribution: %s", base_api_endpoints_url_distribution)
    logger.info(
        "[Digester:InfoMetadata] Base API endpoint (URI, type) distribution: %s",
        base_api_endpoints_type_distribution,
    )
    logger.info(
        "[Digester:InfoMetadata] Heuristic selected values: name=%r applicationVersion=%r apiVersion=%r "
        "apiType=%s baseApiEndpoint=%s",
        found_name,
        found_application_version,
        found_api_version,
        found_api_types,
        [endpoint.model_dump() for endpoint in found_base_api_endpoints],
    )

    merged_response = InfoResponse(
        info_metadata=InfoMetadata(
            name=found_name,
            application_version=found_application_version,
            api_version=found_api_version,
            api_type=found_api_types,
            base_api_endpoint=found_base_api_endpoints,
        )
    )
    merged_payload = cast(Dict[str, Any], merged_response.model_dump(by_alias=True))

    if is_empty_info_result_payload(merged_payload):
        logger.info("[Digester:InfoMetadata] Heuristic result is empty -> returning infoMetadata=null")
        return _empty_info_metadata_payload()

    logger.info("[Digester:InfoMetadata] Heuristic merge produced non-empty infoMetadata")
    return merged_payload
