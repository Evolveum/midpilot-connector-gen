# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Load a session's documentation corpus and select chunks by metadata criteria.

Loading and selection are separate on purpose: a caller that needs several
selections (a primary and a fallback attempt) loads the corpus once and applies
each criteria set to the same snapshot with :func:`select_documentation_items`.
"""

from typing import Any, Dict, List
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.database.repositories.documentation_repository import DocumentationRepository
from src.database.repositories.session_repository import SessionRepository
from src.documents.errors import DocumentationSessionNotFoundError, NoDocumentationStoredError
from src.documents.filtering.schema import ChunkFilterCriteria
from src.documents.normalize import normalize_object_class_tag, object_class_tag_variants


async def filter_documentation_items(
    criteria: ChunkFilterCriteria, session_id: UUID, db: AsyncSession | None = None
) -> List[Dict[str, Any]]:
    """
    Load the session's documentation and keep the items that meet ``criteria``.

    :raises DocumentationSessionNotFoundError: when the session does not exist
    :raises NoDocumentationStoredError: when the session has no documentation
    """
    return select_documentation_items(await load_documentation_items(session_id, db=db), criteria)


async def load_documentation_items(session_id: UUID, db: AsyncSession | None = None) -> List[Dict[str, Any]]:
    """
    Load every documentation chunk of a session in the normalized item shape.

    Items carry ``chunkId``, ``docId``, ``source``, ``url``, ``summary``, ``content``
    and ``@metadata``, ordered by chunk creation time.

    :raises DocumentationSessionNotFoundError: when the session does not exist
    :raises NoDocumentationStoredError: when the session has no documentation
    """
    if db is None:
        from src.core.db import async_session_maker

        async with async_session_maker() as session:
            return await _load_documentation_items(session_id, session)
    return await _load_documentation_items(session_id, db)


async def _load_documentation_items(session_id: UUID, db: AsyncSession) -> List[Dict[str, Any]]:
    if not await SessionRepository(db).session_exists(session_id):
        raise DocumentationSessionNotFoundError(session_id)

    raw_items = await DocumentationRepository(db).get_documentation_items_by_session(session_id)
    if not raw_items:
        raise NoDocumentationStoredError(session_id)

    return [
        {
            "chunkId": item.get("chunkId"),
            "docId": item.get("docId"),
            "source": item.get("source"),
            "url": item.get("url"),
            "summary": item.get("summary"),
            "content": item.get("content", ""),
            "@metadata": item.get("metadata", {}) or {},
        }
        for item in raw_items
    ]


def select_documentation_items(
    doc_items: List[Dict[str, Any]],
    criteria: ChunkFilterCriteria,
) -> List[Dict[str, Any]]:
    """Keep the normalized documentation items that meet ``criteria`` (pure, no I/O).

    JSON and YAML specifications are both kept: a YAML spec may come from another product, version or
    scope than a JSON spec, so neither may be dropped without proof that both are the same document.
    """
    object_class_variants: frozenset[str] | None = None
    if criteria.object_class_tags is not None:
        object_class_variants = frozenset().union(
            *(object_class_tag_variants(name) for name in criteria.object_class_tags)
        )

    filtered_items: List[Dict[str, Any]] = []
    for item in doc_items:
        metadata = item.get("@metadata", {})

        # Extract relevant fields from metadata
        token_count = metadata.get("token_count")
        num_endpoints = metadata.get("num_endpoints")
        category = metadata.get("category")
        tags = metadata.get("tags")
        content_type = metadata.get("content_type")

        # Apply filters
        if criteria.min_length is not None and (token_count is None or token_count < criteria.min_length):
            continue
        if criteria.max_length is not None and (token_count is not None and token_count > criteria.max_length):
            continue
        if criteria.min_endpoints_num is not None and (
            num_endpoints is None or num_endpoints < criteria.min_endpoints_num
        ):
            continue
        if criteria.max_endpoints_num is not None and (
            num_endpoints is not None and num_endpoints > criteria.max_endpoints_num
        ):
            continue
        if criteria.allowed_categories is not None:
            category_allowed = category is not None and category in criteria.allowed_categories
            has_override_tag = (
                criteria.category_override_tags is not None
                and tags is not None
                and any(tag.lower().strip() in criteria.category_override_tags for tag in tags)
            )
            if not category_allowed and not has_override_tag:
                continue
        if criteria.excluded_categories is not None and category in criteria.excluded_categories:
            continue
        if criteria.allowed_tags is not None:
            if tags is None or not all(
                any(tag.lower().strip() in allowed_group for tag in tags) for allowed_group in criteria.allowed_tags
            ):
                continue
        if criteria.excluded_tags is not None:
            if tags is not None and any(tag.lower().strip() in criteria.excluded_tags for tag in tags):
                continue
        if object_class_variants is not None:
            if tags is None or not any(
                isinstance(tag, str) and normalize_object_class_tag(tag) in object_class_variants for tag in tags
            ):
                continue
        if criteria.allowed_content_types is not None and (
            content_type is None or content_type not in criteria.allowed_content_types
        ):
            continue
        if criteria.target_app_versions is not None:
            application_version = metadata.get("application_version")
            if application_version is not None and application_version not in criteria.target_app_versions:
                continue
        if not criteria.allow_unknown_app_version:
            application_version = metadata.get("application_version")
            if application_version is None:
                continue

        filtered_items.append(item)

    return filtered_items
