# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from src.common.database.config import async_session_maker
from src.common.database.repositories.session_repository import SessionRepository
from src.common.utils.normalize import normalize_object_class_name
from src.modules.digester.enums import ConfidenceLevel

logger = logging.getLogger(__name__)

CONFIDENCE_PRIORITY: Dict[ConfidenceLevel, int] = {
    ConfidenceLevel.HIGH: 0,
    ConfidenceLevel.MEDIUM: 1,
    ConfidenceLevel.LOW: 2,
}


def confidence_order_key(confidence: Any) -> int:
    """Get sortable confidence rank where lower value means higher priority."""
    if confidence is None:
        return len(CONFIDENCE_PRIORITY)
    return CONFIDENCE_PRIORITY.get(confidence, len(CONFIDENCE_PRIORITY))


def sort_object_class_dicts(object_classes: List[Any]) -> List[Any]:
    """
    Sort object class dicts by confidence (high -> medium -> low), then alphabetically.
    Non-dict items are preserved at the end in original order.
    """
    has_any_confidence = any(
        isinstance(item, dict) and confidence_order_key(item.get("confidence")) < len(CONFIDENCE_PRIORITY)
        for item in object_classes
    )
    if not has_any_confidence:
        return list(object_classes)

    sortable: List[Dict[str, Any]] = []
    passthrough: List[Any] = []
    for item in object_classes:
        if isinstance(item, dict):
            sortable.append(item)
        else:
            passthrough.append(item)

    sorted_classes = sorted(
        sortable,
        key=lambda obj: (
            confidence_order_key(obj.get("confidence")),
            normalize_object_class_name(str(obj.get("name", ""))),
        ),
    )
    return [*sorted_classes, *passthrough]


def find_object_class(object_classes: List[Any], object_class: str) -> Optional[Dict[str, Any]]:
    """Find object class dict by name (case-insensitive)."""
    normalized_name = normalize_object_class_name(object_class)
    for obj_cls in object_classes:
        if not isinstance(obj_cls, dict):
            continue
        name = obj_cls.get("name")
        if isinstance(name, str) and normalize_object_class_name(name) == normalized_name:
            return obj_cls
    return None


def get_relevant_chunks(object_class_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return normalized relevantDocumentations list with valid doc_id/chunk_id objects only."""
    chunks = object_class_data.get("relevantDocumentations", [])
    if not isinstance(chunks, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        doc_id = chunk.get("doc_id") or chunk.get("docId")
        chunk_id = chunk.get("chunk_id") or chunk.get("chunkId")
        if not doc_id or not chunk_id:
            continue
        normalized.append({"doc_id": str(doc_id), "chunk_id": str(chunk_id)})
    return normalized


def upsert_object_class(
    object_classes_output: Dict[str, Any] | None,
    object_class: str,
    object_class_data: Dict[str, Any],
) -> Tuple[Dict[str, Any], bool]:
    """
    Upsert one object class in objectClassesOutput payload.

    Returns:
        Tuple[payload, updated] where updated=False means new item was appended.
    """
    payload: Dict[str, Any] = dict(object_classes_output) if isinstance(object_classes_output, dict) else {}
    object_classes = payload.get("objectClasses", [])
    if not isinstance(object_classes, list):
        object_classes = []

    data = dict(object_class_data)
    data["name"] = object_class

    normalized_name = normalize_object_class_name(object_class)
    for idx, obj_cls in enumerate(object_classes):
        if not isinstance(obj_cls, dict):
            continue
        name = obj_cls.get("name")
        if isinstance(name, str) and normalize_object_class_name(name) == normalized_name:
            object_classes[idx] = data
            payload["objectClasses"] = sort_object_class_dicts(object_classes)
            return payload, True

    object_classes.append(data)
    payload["objectClasses"] = sort_object_class_dicts(object_classes)
    return payload, False


def extract_attributes_from_result(result: Dict[str, Any] | None) -> Dict[str, Any]:
    """Extract normalized attributes dict from extraction result payload."""
    if not isinstance(result, dict):
        return {}
    result_data = result.get("result", result)
    if not isinstance(result_data, dict):
        return {}
    attributes = result_data.get("attributes", {})
    return attributes if isinstance(attributes, dict) else {}


def extract_endpoints_from_result(result: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    """Extract normalized endpoint dicts from extraction result payload."""
    if not isinstance(result, dict):
        return []
    result_data = result.get("result", result)
    if not isinstance(result_data, dict):
        return []

    raw_endpoints = result_data.get("endpoints", [])
    if not isinstance(raw_endpoints, list):
        return []

    endpoints: List[Dict[str, Any]] = []
    for endpoint in raw_endpoints:
        if hasattr(endpoint, "model_dump"):
            endpoints.append(endpoint.model_dump(by_alias=True))
        elif isinstance(endpoint, dict):
            endpoints.append(endpoint)
    return endpoints


async def update_object_class_field_in_session(
    session_id: UUID,
    object_class: str,
    field_name: str,
    field_value: Any,
) -> bool:
    """
    Update one field in a specific object class under objectClassesOutput.

    Returns:
        True if object class was found and session was updated, otherwise False.
    """
    async with async_session_maker() as db:
        repo = SessionRepository(db)
        object_classes_output = await repo.get_session_data(session_id, "objectClassesOutput")
        if not isinstance(object_classes_output, dict):
            return False

        object_classes = object_classes_output.get("objectClasses", [])
        if not isinstance(object_classes, list):
            return False

        target = find_object_class(object_classes, object_class)
        if target is None:
            return False

        target[field_name] = field_value
        object_classes_output["objectClasses"] = sort_object_class_dicts(object_classes)
        await repo.update_session(session_id, {"objectClassesOutput": object_classes_output})
        logger.info(
            "[Digester:ObjectClasses] Updated '%s' field for object class '%s'",
            field_name,
            object_class,
        )
        return True
