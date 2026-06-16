# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Request-path orchestration for digester session results.

This module owns the relevance plumbing that sits between HTTP handlers and the
session store: persisting manual overrides together with their relevant-chunk
evidence, hydrating finished job results with relevance for responses, and
composing the detailed view of a single object class.

Keeping this logic here (instead of in the router) keeps the HTTP layer thin -
routers only parse input, delegate here, and map domain errors to responses.
"""

import logging
from typing import Any, Awaitable, Callable, Dict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database.repositories.relevant_chunk_repository import RelevantChunkRepository
from src.common.database.repositories.session_repository import SessionRepository
from src.common.enums import JobStatus
from src.common.errors import (
    InvalidObjectClassesOutputError,
    ObjectClassesNotFoundError,
    ObjectClassNotFoundError,
)
from src.common.schema import JobStatusMultiDocResponse
from src.common.session.session import get_session_documentation
from src.common.utils.normalize import normalize_object_class_name
from src.common.utils.relevance import (
    build_chunk_to_doc_map,
    extract_attribute_relevance_rows,
    extract_endpoint_relevance_rows,
    extract_object_class_relevance_rows,
    hydrate_attributes_with_relevance,
    hydrate_endpoints_with_relevance,
    hydrate_object_classes_with_relevance,
    load_object_class_relevance_map,
    strip_attributes_relevance,
    strip_endpoints_relevance,
    strip_object_class_relevance,
)
from src.modules.digester.entities.object_classes import find_object_class, upsert_object_class
from src.modules.digester.schemas import (
    AttributeResponse,
    ConnectivityEndpointResponse,
    EndpointResponse,
    ObjectClassesResponse,
)

logger = logging.getLogger(__name__)


# Result keys
OBJECT_CLASSES_RESULT_KEY = "objectClassesOutput"
CONNECTIVITY_ENDPOINT_RESULT_KEY = "connectivityEndpointOutput"


def attributes_result_key(object_class: str) -> str:
    return f"{normalize_object_class_name(object_class)}AttributesOutput"


def endpoints_result_key(object_class: str) -> str:
    return f"{normalize_object_class_name(object_class)}EndpointsOutput"


# Internal helpers
def _safe_exception_summary(exc: Exception) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            return repr(errors(include_input=False))
        except TypeError:
            return type(exc).__name__
    return type(exc).__name__


def _log_status_result_fallback(
    session_id: UUID,
    result_key: str,
    response_model: type[Any],
    exc: Exception,
) -> None:
    logger.warning(
        "[Digester:Results] Failed to hydrate session result; keeping original job result. session_id=%s result_key=%s response_model=%s error=%s",
        session_id,
        result_key,
        response_model.__name__,
        _safe_exception_summary(exc),
    )


async def _store_result_with_relevance(
    db: AsyncSession,
    repo: SessionRepository,
    session_id: UUID,
    result_key: str,
    stripped_payload: Dict[str, Any],
    relevance_rows: list[Dict[str, Any]],
) -> None:
    await repo.update_session(session_id, {result_key: stripped_payload})
    relevant_repo = RelevantChunkRepository(db)
    await relevant_repo.replace_relevant_chunks_for_result(
        session_id=session_id,
        result_key=result_key,
        chunks=relevance_rows,
    )


async def _refresh_finished_status_result(
    response: JobStatusMultiDocResponse,
    repo: SessionRepository,
    session_id: UUID,
    result_key: str,
    response_model: type[Any],
    hydrate_payload: Callable[[Any], Awaitable[Any]],
) -> JobStatusMultiDocResponse:
    if response.status != JobStatus.finished:
        return response

    session_output = await repo.get_session_data(session_id, result_key)
    if not session_output:
        return response

    try:
        hydrated_output = await hydrate_payload(session_output)
        response.result = response_model.model_validate(hydrated_output)
    except Exception as exc:
        _log_status_result_fallback(session_id, result_key, response_model, exc)

    return response


# Status hydration (GET handlers)
async def refresh_object_classes_status(
    db: AsyncSession,
    repo: SessionRepository,
    response: JobStatusMultiDocResponse,
    session_id: UUID,
) -> JobStatusMultiDocResponse:
    async def hydrate_payload(payload: Any) -> Any:
        return await hydrate_object_classes_with_relevance(db, session_id, payload)

    return await _refresh_finished_status_result(
        response,
        repo,
        session_id,
        OBJECT_CLASSES_RESULT_KEY,
        ObjectClassesResponse,
        hydrate_payload,
    )


async def refresh_attributes_status(
    db: AsyncSession,
    repo: SessionRepository,
    response: JobStatusMultiDocResponse,
    session_id: UUID,
    object_class: str,
) -> JobStatusMultiDocResponse:
    result_key = attributes_result_key(object_class)

    async def hydrate_payload(payload: Any) -> Any:
        return await hydrate_attributes_with_relevance(db, session_id, result_key, payload)

    return await _refresh_finished_status_result(
        response,
        repo,
        session_id,
        result_key,
        AttributeResponse,
        hydrate_payload,
    )


async def refresh_endpoints_status(
    db: AsyncSession,
    repo: SessionRepository,
    response: JobStatusMultiDocResponse,
    session_id: UUID,
    object_class: str,
) -> JobStatusMultiDocResponse:
    result_key = endpoints_result_key(object_class)

    async def hydrate_payload(payload: Any) -> Any:
        return await hydrate_endpoints_with_relevance(db, session_id, result_key, payload)

    return await _refresh_finished_status_result(
        response,
        repo,
        session_id,
        result_key,
        EndpointResponse,
        hydrate_payload,
    )


async def refresh_connectivity_endpoint_status(
    db: AsyncSession,
    repo: SessionRepository,
    response: JobStatusMultiDocResponse,
    session_id: UUID,
) -> JobStatusMultiDocResponse:
    async def hydrate_payload(payload: Any) -> Any:
        return await hydrate_endpoints_with_relevance(db, session_id, CONNECTIVITY_ENDPOINT_RESULT_KEY, payload)

    return await _refresh_finished_status_result(
        response,
        repo,
        session_id,
        CONNECTIVITY_ENDPOINT_RESULT_KEY,
        ConnectivityEndpointResponse,
        hydrate_payload,
    )


# Override storage (PUT handlers)
async def store_object_classes(
    db: AsyncSession,
    repo: SessionRepository,
    session_id: UUID,
    object_classes_data: Dict[str, Any],
) -> None:
    """Replace all object classes in the session, persisting relevance separately."""
    relevance_rows = extract_object_class_relevance_rows(object_classes_data)
    stripped_payload = strip_object_class_relevance(object_classes_data)
    await _store_result_with_relevance(
        db, repo, session_id, OBJECT_CLASSES_RESULT_KEY, stripped_payload, relevance_rows
    )


async def upsert_object_class_in_session(
    db: AsyncSession,
    repo: SessionRepository,
    session_id: UUID,
    object_class: str,
    object_class_data: Dict[str, Any],
) -> bool:
    """Insert or update a single object class. Returns True when an existing class was updated."""
    object_classes_output = await repo.get_session_data(session_id, OBJECT_CLASSES_RESULT_KEY)
    object_classes_output, updated = upsert_object_class(object_classes_output, object_class, object_class_data)
    relevance_rows = extract_object_class_relevance_rows(object_classes_output)
    stripped_payload = strip_object_class_relevance(object_classes_output)
    await _store_result_with_relevance(
        db, repo, session_id, OBJECT_CLASSES_RESULT_KEY, stripped_payload, relevance_rows
    )
    return updated


async def store_attributes_override(
    db: AsyncSession,
    repo: SessionRepository,
    session_id: UUID,
    object_class: str,
    attributes: Dict[str, Any],
) -> None:
    result_key = attributes_result_key(object_class)
    stripped_attributes = strip_attributes_relevance(attributes)
    chunk_to_doc = build_chunk_to_doc_map(await get_session_documentation(session_id, db=db))
    relevance_rows = extract_attribute_relevance_rows(attributes, result_key, chunk_to_doc=chunk_to_doc)
    await _store_result_with_relevance(db, repo, session_id, result_key, stripped_attributes, relevance_rows)


async def store_endpoints_override(
    db: AsyncSession,
    repo: SessionRepository,
    session_id: UUID,
    object_class: str,
    endpoints: Dict[str, Any],
) -> None:
    result_key = endpoints_result_key(object_class)
    stripped_endpoints = strip_endpoints_relevance(endpoints)
    relevance_rows = extract_endpoint_relevance_rows(endpoints, result_key)
    await _store_result_with_relevance(db, repo, session_id, result_key, stripped_endpoints, relevance_rows)


async def store_connectivity_endpoint_override(
    db: AsyncSession,
    repo: SessionRepository,
    session_id: UUID,
    payload: Dict[str, Any],
) -> None:
    stripped_payload = strip_endpoints_relevance(payload)
    relevance_rows = extract_endpoint_relevance_rows(payload, CONNECTIVITY_ENDPOINT_RESULT_KEY)
    await _store_result_with_relevance(
        db, repo, session_id, CONNECTIVITY_ENDPOINT_RESULT_KEY, stripped_payload, relevance_rows
    )


# --------------------------------------------------------------------------- #
# Composite read (GET single object class)
# --------------------------------------------------------------------------- #


async def build_object_class_detail(
    db: AsyncSession,
    repo: SessionRepository,
    session_id: UUID,
    object_class: str,
) -> Dict[str, Any]:
    """Assemble a single object class enriched with relevance, attributes and endpoints.

    Raises ObjectClassesNotFoundError / InvalidObjectClassesOutputError /
    ObjectClassNotFoundError so the caller can map them to HTTP 404.
    """
    object_classes_output = await repo.get_session_data(session_id, OBJECT_CLASSES_RESULT_KEY)
    if not object_classes_output or not isinstance(object_classes_output, dict):
        raise ObjectClassesNotFoundError(session_id)

    object_classes = object_classes_output.get("objectClasses", [])
    if not isinstance(object_classes, list):
        raise InvalidObjectClassesOutputError(session_id)

    target_object_class = find_object_class(object_classes, object_class)
    if not target_object_class:
        raise ObjectClassNotFoundError(object_class, session_id)

    result = target_object_class.copy()
    normalized_name = normalize_object_class_name(object_class)

    try:
        relevance_map = await load_object_class_relevance_map(db, session_id)
        result["relevantDocumentations"] = relevance_map.get(normalized_name, [])
    except Exception as exc:
        logger.warning(
            "[Digester:Results] Failed to load object class relevance map; using stored relevance. session_id=%s object_class=%s error=%s",
            session_id,
            object_class,
            _safe_exception_summary(exc),
        )
        result["relevantDocumentations"] = result.get("relevantDocumentations", [])

    attributes_output = await repo.get_session_data(session_id, f"{normalized_name}AttributesOutput")
    if attributes_output and isinstance(attributes_output, dict):
        hydrated_attributes = await hydrate_attributes_with_relevance(
            db,
            session_id,
            f"{normalized_name}AttributesOutput",
            attributes_output,
        )
        if isinstance(hydrated_attributes.get("attributes"), dict):
            result["attributes"] = hydrated_attributes.get("attributes", {})
        else:
            result["attributes"] = hydrated_attributes

    endpoints_output = await repo.get_session_data(session_id, f"{normalized_name}EndpointsOutput")
    if endpoints_output and isinstance(endpoints_output, dict):
        hydrated_endpoints = await hydrate_endpoints_with_relevance(
            db,
            session_id,
            f"{normalized_name}EndpointsOutput",
            endpoints_output,
        )
        if isinstance(hydrated_endpoints.get("endpoints"), list):
            result["endpoints"] = hydrated_endpoints.get("endpoints", [])
        else:
            result["endpoints"] = []

    return result
