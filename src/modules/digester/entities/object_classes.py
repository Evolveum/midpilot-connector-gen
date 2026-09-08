# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Any, Dict, List, Literal, Optional, Tuple
from uuid import UUID

from src.core.db import async_session_maker
from src.core.job_execution import get_current_execution
from src.database.repositories.job_repository import JobRepository
from src.database.repositories.session_repository import SessionRepository
from src.documents.normalize import normalize_object_class_name
from src.modules.digester.enums import ConfidenceLevel
from src.modules.digester.errors import (
    InvalidObjectClassesOutputError,
    ObjectClassesNotFoundError,
    ObjectClassNotFoundError,
)
from src.shared.coerce import as_dict_list, as_list

logger = logging.getLogger(__name__)

CONFIDENCE_PRIORITY: Dict[ConfidenceLevel, int] = {
    ConfidenceLevel.HIGH: 0,
    ConfidenceLevel.MEDIUM: 1,
    ConfidenceLevel.LOW: 2,
}
ObjectClassResultField = Literal["attributes", "endpoints"]
_POINTER_SUFFIX_BY_FIELD: dict[ObjectClassResultField, str] = {
    "attributes": "Attributes",
    "endpoints": "Endpoints",
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


async def resolve_object_class(repo: SessionRepository, session_id: UUID, object_class: str) -> Dict[str, Any]:
    """Resolve a stored class using the established digester read/error contract."""
    output = await repo.get_session_data(session_id, "objectClassesOutput")
    if not output or not isinstance(output, dict):
        raise ObjectClassesNotFoundError(session_id)
    classes = output.get("objectClasses", [])
    if not isinstance(classes, list):
        raise InvalidObjectClassesOutputError(session_id)
    target = find_object_class(classes, object_class)
    if target is None:
        raise ObjectClassNotFoundError(object_class, session_id)
    return target


def find_object_class(object_classes: List[Any], object_class: str) -> Optional[Dict[str, Any]]:
    """Find object class dict by name (case-insensitive)."""
    normalized_name = normalize_object_class_name(object_class)
    for obj_cls in as_dict_list(object_classes):
        name = obj_cls.get("name")
        if isinstance(name, str) and normalize_object_class_name(name) == normalized_name:
            return obj_cls
    return None


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


def build_attribute_result(
    attributes: Dict[str, Any] | None = None,
    relevant_documentations: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """
    Build the standard attribute-extraction result payload.

    Counterpart to :func:`extract_attributes_from_result`. Called with no arguments it
    returns the empty result used when there is no usable documentation to extract from.
    """
    return {
        "result": {"attributes": {} if attributes is None else attributes},
        "relevantDocumentations": [] if relevant_documentations is None else relevant_documentations,
    }


def extract_endpoints_from_result(result: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    """Extract normalized endpoint dicts from extraction result payload."""
    if not isinstance(result, dict):
        return []
    result_data = result.get("result", result)
    if not isinstance(result_data, dict):
        return []

    endpoints: List[Dict[str, Any]] = []
    for endpoint in as_list(result_data.get("endpoints")):
        if hasattr(endpoint, "model_dump"):
            endpoints.append(endpoint.model_dump(by_alias=True))
        elif isinstance(endpoint, dict):
            endpoints.append(endpoint)
    return endpoints


def build_endpoint_result(
    endpoints: List[Dict[str, Any]] | None = None,
    relevant_documentations: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """
    Build the standard endpoint-extraction result payload.

    Counterpart to :func:`extract_endpoints_from_result`. Called with no arguments it
    returns the empty result used when there is no usable documentation to extract from.
    """
    return {
        "result": {"endpoints": [] if endpoints is None else endpoints},
        "relevantDocumentations": [] if relevant_documentations is None else relevant_documentations,
    }


async def update_object_class_field_in_session(
    session_id: UUID,
    object_class: str,
    field_name: ObjectClassResultField,
    field_value: Any,
) -> bool:
    """
    Update one field in a specific object class under objectClassesOutput.

    Returns:
        True if object class was found and session was updated, otherwise False.
    """
    async with async_session_maker() as db:
        repo = SessionRepository(db)
        execution = get_current_execution()
        if execution is not None:
            await JobRepository(db).acquire_execution_fence(
                execution.job_id,
                worker_id=execution.worker_id,
                execution_token=execution.execution_token,
            )
        if not await repo.lock_session(session_id):
            return False

        if execution is not None:
            pointer_key = f"{object_class}{_POINTER_SUFFIX_BY_FIELD[field_name]}JobId"
            if not await repo.is_current_job_pointer(
                session_id=session_id,
                pointer_key=pointer_key,
                job_id=execution.job_id,
                lock=True,
            ):
                return False

        object_classes_output = await repo.get_session_value(session_id, "objectClassesOutput")
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
        await repo.update_locked_session(session_id, {"objectClassesOutput": object_classes_output})
        await db.commit()
        logger.info(
            "[Digester:ObjectClasses] Updated '%s' field for object class '%s'",
            field_name,
            object_class,
        )
        return True
