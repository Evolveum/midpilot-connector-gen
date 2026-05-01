# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.modules.digester import service
from src.modules.digester.schema import ExtendedObjectClass


# ==================== EXTRACT OBJECT CLASSES ====================
@pytest.mark.asyncio
async def test_extract_object_classes_success(mock_llm, mock_digester_update_job_progress):
    """
    Test extracting object classes from multiple documentation items.
    Validates metadata tracking, deduplication, and class-to-chunk mapping.
    """
    doc_uuid1 = uuid4()
    doc_uuid2 = uuid4()

    fake_doc_items = [
        {
            "uuid": str(doc_uuid1),
            "content": "User management API documentation",
            "summary": "User API docs",
            "@metadata": {"source": "api_spec", "category": "reference_api"},
        },
        {
            "uuid": str(doc_uuid2),
            "content": "Group management API documentation",
            "summary": "Group API docs",
            "@metadata": {"source": "api_spec", "category": "reference_api"},
        },
    ]

    with (
        patch("src.modules.digester.service.deduplicate_and_sort_object_classes") as mock_dedupe,
        patch("src.modules.digester.service.run_doc_extractors_concurrently") as mock_parallel,
        patch("src.modules.digester.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
    ):
        mock_parallel.return_value = [
            (
                [
                    ExtendedObjectClass(
                        name="User",
                        superclass=None,
                        abstract=False,
                        embedded=False,
                        description="Represents a user in the system",
                    ),
                ],
                True,
                doc_uuid1,
            ),
            (
                [
                    ExtendedObjectClass(
                        name="Group",
                        superclass=None,
                        abstract=False,
                        embedded=False,
                        description="Represents a group of users",
                    ),
                ],
                True,
                doc_uuid2,
            ),
        ]

        class FakeDeduped:
            def model_dump(self, by_alias=True):
                return {
                    "objectClasses": [
                        {
                            "name": "User",
                            "relevant": "true",
                            "confidence": "high",
                            "description": "Represents a user in the system",
                            "relevantDocumentations": [{"docId": str(doc_uuid1), "chunkId": str(doc_uuid1)}],
                        },
                        {
                            "name": "Group",
                            "relevant": "true",
                            "confidence": "medium",
                            "description": "Represents a group of users",
                            "relevantDocumentations": [{"docId": str(doc_uuid2), "chunkId": str(doc_uuid2)}],
                        },
                    ]
                }

        mock_dedupe.return_value = FakeDeduped()

        job_id = uuid4()
        session_id = uuid4()
        result = await service.extract_object_classes(fake_doc_items, job_id, session_id)

        assert "result" in result
        assert "relevantDocumentations" in result
        assert "objectClasses" in result["result"]
        assert len(result["result"]["objectClasses"]) == 2
        assert result["result"]["objectClasses"][0]["name"] == "User"
        assert result["result"]["objectClasses"][1]["name"] == "Group"

        mock_parallel.assert_called_once()


@pytest.mark.asyncio
async def test_extract_object_classes_empty_docs(mock_llm, mock_digester_update_job_progress):
    """Test extract_object_classes with no documentation items."""
    with (
        patch("src.modules.digester.service.deduplicate_and_sort_object_classes") as mock_dedupe,
        patch("src.modules.digester.service.run_doc_extractors_concurrently") as mock_parallel,
        patch("src.modules.digester.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
    ):
        mock_parallel.return_value = []

        class EmptyDeduped:
            def model_dump(self, by_alias=True):
                return {"objectClasses": []}

        mock_dedupe.return_value = EmptyDeduped()

        result = await service.extract_object_classes([], uuid4(), uuid4())

        assert result["result"]["objectClasses"] == []
        assert result["relevantDocumentations"] == []
