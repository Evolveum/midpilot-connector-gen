# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for connector-fix relevant documentation selection."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.modules.codegen.selection.relevant_chunks import collect_connector_relevant_chunks


@pytest.mark.asyncio
async def test_fix_collects_operation_and_matching_object_class_chunks_without_a_limit():
    session_id = uuid4()
    db_context = MagicMock()
    db_context.__aenter__ = AsyncMock(return_value=MagicMock())
    db_context.__aexit__ = AsyncMock(return_value=None)

    repo = MagicMock()
    repo.get_relevant_chunks_map = AsyncMock(
        return_value={
            "userEndpointsOutput": [
                {"chunkId": "endpoint", "docId": "doc-endpoint"},
                {"chunkId": "shared", "docId": "doc-shared"},
            ],
            "userAttributesOutput": [
                {"chunkId": "shared", "docId": "doc-shared"},
                {"chunkId": "attribute", "docId": "doc-attribute"},
            ],
        }
    )
    repo.get_relevant_chunks = AsyncMock(
        return_value=[
            {"chunkId": "object-class", "docId": "doc-object-class", "entityKey": "user"},
            {"chunkId": "shared", "docId": "doc-shared", "entityKey": "user"},
        ]
    )

    with (
        patch("src.modules.codegen.selection.relevant_chunks.async_session_maker", return_value=db_context),
        patch("src.modules.codegen.selection.relevant_chunks.RelevantChunkRepository", return_value=repo),
    ):
        selected = await collect_connector_relevant_chunks(session_id, [" User "])

    assert selected == [
        {"chunk_id": "endpoint", "doc_id": "doc-endpoint"},
        {"chunk_id": "shared", "doc_id": "doc-shared"},
        {"chunk_id": "attribute", "doc_id": "doc-attribute"},
        {"chunk_id": "object-class", "doc_id": "doc-object-class"},
    ]
    repo.get_relevant_chunks_map.assert_awaited_once_with(
        session_id,
        result_keys=["userEndpointsOutput", "userAttributesOutput"],
    )
    repo.get_relevant_chunks.assert_awaited_once_with(
        session_id=session_id,
        result_key="objectClassesOutput",
        entity_key="user",
    )


@pytest.mark.asyncio
async def test_fix_with_no_object_class_does_not_query_relevance():
    with patch("src.modules.codegen.selection.relevant_chunks.async_session_maker") as session_maker:
        selected = await collect_connector_relevant_chunks(uuid4(), ["", "   "])

    assert selected == []
    session_maker.assert_not_called()
