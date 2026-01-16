"""
This module provides functionality to filter chunks based on specific criteria.
It is designed to be used by digestor and codegen services.
"""

#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

from typing import Any, Dict, List
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..database.repositories.documentation_repository import DocumentationRepository
from ..database.repositories.session_repository import SessionRepository
from .schema import ChunkFilterCriteria


async def filter_documentation_items(
    criteria: ChunkFilterCriteria, session_id: UUID, db: AsyncSession | None = None
) -> List[Dict[str, Any]]:
    """
    Filters documentation items based on the provided criteria.
    Works directly with documentationItems without reconstructing PageChunk objects.

    input: criteria - ChunkFilterCriteria object defining the filtering conditions
           session_id - session ID to retrieve documentation items from
           db - optional SQLAlchemy AsyncSession
    output: list of documentationItem dicts that meet the criteria
    """
    if db is None:
        from ..database.config import async_session_maker

        async with async_session_maker() as session:
            return await _filter_documentation_items_impl(criteria, session_id, session)
    else:
        return await _filter_documentation_items_impl(criteria, session_id, db)


async def _filter_documentation_items_impl(
    criteria: ChunkFilterCriteria, session_id: UUID, db: AsyncSession
) -> List[Dict[str, Any]]:
    session_repo = SessionRepository(db)
    if not await session_repo.session_exists(session_id):
        raise ValueError(f"Session with ID {session_id} does not exist.")

    doc_repo = DocumentationRepository(db)
    raw_items = await doc_repo.get_documentation_items_by_session(session_id)
    if not raw_items:
        raise ValueError(f"Session with ID {session_id} has no documentation items stored.")

    doc_items: List[Dict[str, Any]] = []
    for item in raw_items:
        doc_items.append(
            {
                "uuid": item.get("id"),
                "pageId": item.get("pageId"),
                "source": item.get("source"),
                "url": item.get("url"),
                "summary": item.get("summary"),
                "content": item.get("content", ""),
                "@metadata": item.get("metadata", {}) or {},
            }
        )

    # Filter documentation items based on criteria
    filtered_items: List[Dict[str, Any]] = []
    for item in doc_items:
        metadata = item.get("@metadata", {})

        # Extract relevant fields from metadata
        length = metadata.get("length")
        num_endpoints = metadata.get("num_endpoints")
        category = metadata.get("category")
        tags = metadata.get("tags")
        content_type = metadata.get("contentType")

        # Apply filters
        if criteria.min_length is not None and (length is None or length < criteria.min_length):
            continue
        if criteria.max_length is not None and (length is not None and length > criteria.max_length):
            continue
        if criteria.min_endpoints_num is not None and (
            num_endpoints is None or num_endpoints < criteria.min_endpoints_num
        ):
            continue
        if criteria.max_endpoints_num is not None and (
            num_endpoints is not None and num_endpoints > criteria.max_endpoints_num
        ):
            continue
        if criteria.allowed_categories is not None and (
            category is None or category not in criteria.allowed_categories
        ):
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
        if criteria.allowed_content_types is not None and (
            content_type is None or content_type not in criteria.allowed_content_types
        ):
            continue

        filtered_items.append(item)

    # Post-filtering: If both spec_yaml and spec_json exist, keep only spec_yaml
    filtered_items = _prioritize_yaml_over_json(filtered_items)

    return filtered_items


def _prioritize_yaml_over_json(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    If both spec_yaml and spec_json categories exist in the items, remove spec_json items.

    Args:
        items: List of documentation items

    Returns:
        Filtered list with spec_json removed if spec_yaml exists
    """
    # Check if both categories exist
    categories = {item.get("@metadata", {}).get("category") for item in items}
    has_spec_yaml = "spec_yaml" in categories
    has_spec_json = "spec_json" in categories

    # If both exist, filter out spec_json
    if has_spec_yaml and has_spec_json:
        return [item for item in items if item.get("@metadata", {}).get("category") != "spec_json"]

    return items
