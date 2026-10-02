# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.documents.errors import DocumentationSessionNotFoundError
from src.documents.filtering.filter import filter_documentation_items, select_documentation_items
from src.documents.filtering.schema import ChunkFilterCriteria


def _raw_item(chunk_id: str, *, category: str, tags=None):
    return {
        "chunkId": chunk_id,
        "docId": str(uuid4()),
        "source": "doc",
        "url": "http://example.test",
        "summary": "",
        "content": "content",
        "metadata": {"category": category, "tags": tags},
    }


async def _run_filter(criteria: ChunkFilterCriteria, raw_items):
    session_id = uuid4()
    db = AsyncMock()
    with (
        patch("src.documents.filtering.filter.SessionRepository") as session_repo_cls,
        patch("src.documents.filtering.filter.DocumentationRepository") as doc_repo_cls,
    ):
        session_repo_cls.return_value.session_exists = AsyncMock(return_value=True)
        doc_repo_cls.return_value.get_documentation_items_by_session = AsyncMock(return_value=raw_items)
        return await filter_documentation_items(criteria, session_id, db=db)


@pytest.mark.asyncio
async def test_category_override_tags_keeps_non_allowed_category_with_matching_tag():
    criteria = ChunkFilterCriteria(
        min_length=None,
        min_endpoints_num=None,
        allowed_categories=["spec_yaml"],
        category_override_tags=["scim", "rest", "provisioning"],
    )
    items = [
        _raw_item("spec", category="spec_yaml"),
        _raw_item("overview-scim", category="overview", tags=["SCIM", "User"]),
        _raw_item("overview-other", category="overview", tags=["pricing"]),
    ]

    result = await _run_filter(criteria, items)

    kept = {item["chunkId"] for item in result}
    assert kept == {"spec", "overview-scim"}


@pytest.mark.asyncio
async def test_category_override_tags_match_is_case_insensitive():
    criteria = ChunkFilterCriteria(
        min_length=None,
        min_endpoints_num=None,
        allowed_categories=["spec_yaml"],
        category_override_tags=["rest"],
    )
    items = [_raw_item("rest-overview", category="overview", tags=["REST"])]

    result = await _run_filter(criteria, items)

    assert [item["chunkId"] for item in result] == ["rest-overview"]


@pytest.mark.asyncio
async def test_without_override_tags_non_allowed_category_is_excluded():
    criteria = ChunkFilterCriteria(
        min_length=None,
        min_endpoints_num=None,
        allowed_categories=["spec_yaml"],
    )
    items = [
        _raw_item("spec", category="spec_yaml"),
        _raw_item("overview-scim", category="overview", tags=["SCIM"]),
    ]

    result = await _run_filter(criteria, items)

    assert [item["chunkId"] for item in result] == ["spec"]


@pytest.mark.asyncio
async def test_override_tag_still_respects_excluded_categories():
    criteria = ChunkFilterCriteria(
        min_length=None,
        min_endpoints_num=None,
        allowed_categories=["spec_yaml"],
        excluded_categories=["overview"],
        category_override_tags=["scim"],
    )
    items = [_raw_item("overview-scim", category="overview", tags=["SCIM"])]

    result = await _run_filter(criteria, items)

    assert result == []


@pytest.mark.asyncio
async def test_missing_session_raises_documents_domain_error_with_session_not_found_contract():
    session_id = uuid4()
    with patch("src.documents.filtering.filter.SessionRepository") as session_repo_cls:
        session_repo_cls.return_value.session_exists = AsyncMock(return_value=False)
        with pytest.raises(DocumentationSessionNotFoundError) as exc_info:
            await filter_documentation_items(ChunkFilterCriteria(), session_id, db=AsyncMock())

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "session_not_found"
    assert exc_info.value.message == f"Session {session_id} not found"


def test_select_documentation_items_applies_criteria_without_loading_anything():
    criteria = ChunkFilterCriteria(min_length=None, min_endpoints_num=None, allowed_categories=["spec_yaml"])
    items = [
        {"chunkId": "keep", "@metadata": {"category": "spec_yaml"}},
        {"chunkId": "drop", "@metadata": {"category": "overview"}},
    ]

    assert [item["chunkId"] for item in select_documentation_items(items, criteria)] == ["keep"]
