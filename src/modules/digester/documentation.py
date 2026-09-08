# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Resolve wizard selections to their persisted source documentation."""

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.database.repositories.documentation_repository import DocumentationRepository
from src.database.repositories.session_repository import SessionRepository
from src.documents.normalize import normalize_endpoint_key, normalize_object_class_name
from src.documents.relevance import build_endpoint_entity_key
from src.documents.schemas import RelevantDocumentationItem
from src.modules.digester.entities.object_classes import resolve_object_class
from src.modules.digester.enums import EndpointMethod
from src.modules.digester.errors import (
    DocumentationEndpointNotFoundError,
    InvalidDocumentationFilterError,
    InvalidEndpointsOutputError,
)
from src.modules.digester.results import OBJECT_CLASSES_RESULT_KEY, endpoints_result_key
from src.modules.digester.schemas.documentation import (
    DocumentationEndpoint,
    RelevantDocumentationResponse,
)

logger = logging.getLogger(__name__)


async def get_relevant_documentation(
    db: AsyncSession,
    repo: SessionRepository,
    session_id: UUID,
    object_class: str,
    *,
    method: EndpointMethod | None,
    path: str | None,
    offset: int,
    limit: int,
) -> RelevantDocumentationResponse:
    if (method is None) != (path is None) or (path is not None and not path.strip()):
        raise InvalidDocumentationFilterError()

    target = await resolve_object_class(repo, session_id, object_class)

    result_key = OBJECT_CLASSES_RESULT_KEY
    entity_key = normalize_object_class_name(object_class)
    endpoint = None
    if method is not None and path is not None:
        result_key = endpoints_result_key(object_class)
        endpoints_output = await repo.get_session_data(session_id, result_key)
        if endpoints_output is None:
            raise DocumentationEndpointNotFoundError()
        if not isinstance(endpoints_output, dict) or not isinstance(endpoints_output.get("endpoints"), list):
            raise InvalidEndpointsOutputError()
        key = normalize_endpoint_key(path, method)
        match = next(
            (
                item
                for item in endpoints_output["endpoints"]
                if isinstance(item, dict) and normalize_endpoint_key(item.get("path"), item.get("method")) == key
            ),
            None,
        )
        if match is None:
            raise DocumentationEndpointNotFoundError()
        endpoint = DocumentationEndpoint(method=method, path=path.strip())
        endpoint_key = build_endpoint_entity_key(endpoint.path, endpoint.method)
        assert endpoint_key is not None
        entity_key = endpoint_key

    rows = await DocumentationRepository(db).get_relevant_documentation_items(
        session_id,
        result_key=result_key,
        entity_key=entity_key,
        offset=offset,
        limit=limit + 1,
    )
    items = []
    for row in rows[:limit]:
        metadata = row.get("metadata") or {}
        items.append(
            RelevantDocumentationItem(
                doc_id=row["docId"],
                chunk_id=row["chunkId"],
                content=row["content"],
                source=row["source"],
                url=row.get("url"),
                filename=metadata.get("filename"),
                content_type=metadata.get("content_type"),
                chunk_number=metadata.get("chunk_number"),
            )
        )
    logger.debug(
        "[Digester:Documentation] Returned %s chunks for class %s result %s entity %s offset %s has_more=%s",
        len(items),
        target["name"],
        result_key,
        entity_key,
        offset,
        len(rows) > limit,
    )
    return RelevantDocumentationResponse(
        object_class=target["name"],
        endpoint=endpoint,
        items=items,
        next_offset=offset + limit if len(rows) > limit else None,
    )
