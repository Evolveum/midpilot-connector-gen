# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for codegen service helper utilities."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.modules.codegen import service
from src.modules.digester.schemas import RelationsResponse


def test_collect_pairs_new_format():
    """Test _collect_pairs with format containing chunk_id."""
    input_data = [
        {"chunk_id": "uuid1"},
        {"chunk_id": "uuid2"},
        {"chunk_id": "uuid3"},
    ]

    result = service._collect_pairs(input_data)

    expected = [(0, "uuid1"), (1, "uuid2"), (2, "uuid3")]
    assert result == expected


def test_collect_pairs_legacy_format():
    """Test _collect_pairs with legacy format containing only integers."""
    # Legacy format: list of integers
    input_data = [1, 2, 3, 4]

    result = service._collect_pairs(input_data)

    expected = [(1, None), (2, None), (3, None), (4, None)]
    assert result == expected


def test_collect_pairs_empty_input():
    """Test _collect_pairs with empty or None input."""
    assert service._collect_pairs(None) == []
    assert service._collect_pairs([]) == []
    assert service._collect_pairs("") == []


@pytest.mark.asyncio
async def test_collect_relation_object_class_pairs_uses_subject_and_object_chunks():
    relations = RelationsResponse.model_validate(
        {
            "relations": [
                {
                    "name": "principal_to_membership",
                    "displayName": "Principal to Membership",
                    "shortDescription": "",
                    "subject": "principal",
                    "subjectAttribute": "memberships",
                    "object": "membership",
                    "objectAttribute": "",
                }
            ]
        }
    )
    with (
        patch("src.modules.codegen.service.async_session_maker") as mock_session_maker,
        patch("src.modules.codegen.service.RelevantChunkRepository") as mock_relevant_repository,
    ):
        mock_db_cm = mock_session_maker.return_value
        mock_db = AsyncMock()
        mock_db_cm.__aenter__.return_value = mock_db

        mock_repo_instance = mock_relevant_repository.return_value
        mock_repo_instance.get_relevant_chunks_grouped_by_entity = AsyncMock(
            return_value={
                "principal": [
                    {"docId": "doc-1", "chunkId": "principal-1"},
                    {"docId": "doc-2", "chunkId": "shared"},
                ],
                "membership": [
                    {"docId": "doc-2", "chunkId": "shared"},
                    {"docId": "doc-3", "chunkId": "membership-1"},
                ],
                "role": [{"docId": "doc-4", "chunkId": "role-1"}],
            }
        )

        result = await service._collect_relation_object_class_pairs(relations, uuid4())

        assert result == [
            {"doc_id": "doc-1", "chunk_id": "principal-1"},
            {"doc_id": "doc-2", "chunk_id": "shared"},
            {"doc_id": "doc-3", "chunk_id": "membership-1"},
        ]
