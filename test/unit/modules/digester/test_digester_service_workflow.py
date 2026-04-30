# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.modules.digester import service
from src.modules.digester.schema import ExtendedObjectClass


# ==================== INTEGRATION SCENARIOS ====================
@pytest.mark.asyncio
async def test_full_workflow_object_class_to_endpoints(mock_llm, mock_digester_update_job_progress):
    """
    Integration test simulating the full workflow:
    1. Extract object classes
    2. Extract attributes for a class
    3. Extract endpoints for a class
    """
    session_id = uuid4()
    doc_uuid = uuid4()

    doc_items = [
        {
            "uuid": str(doc_uuid),
            "content": "Complete API documentation with User schema and endpoints",
            "summary": "Full API docs",
            "@metadata": {"tags": "spec"},
        }
    ]

    with (
        patch("src.modules.digester.service.update_job_progress", new_callable=AsyncMock),
    ):
        # Step 1: Extract object classes
        with (
            patch("src.modules.digester.service.build_object_class_extraction_chain", return_value=object()),
            patch(
                "src.modules.digester.service.run_doc_extractors_concurrently", new_callable=AsyncMock
            ) as mock_parallel,
            patch(
                "src.modules.digester.service.deduplicate_and_sort_object_classes", new_callable=AsyncMock
            ) as mock_dedupe_classes,
            patch("src.modules.digester.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
        ):
            mock_parallel.return_value = [
                (
                    [
                        ExtendedObjectClass(
                            name="User",
                            description="User entity",
                        )
                    ],
                    True,
                    doc_uuid,
                )
            ]

            class ObjectClassResult:
                def model_dump(self, by_alias=True):
                    return {
                        "objectClasses": [
                            {
                                "name": "User",
                                "relevant": "true",
                                "confidence": "high",
                                "description": "User entity",
                                "relevantDocumentations": [{"docId": str(doc_uuid), "chunkId": str(doc_uuid)}],
                            }
                        ]
                    }

            mock_dedupe_classes.return_value = ObjectClassResult()

            classes_result = await service.extract_object_classes(doc_items, uuid4(), session_id)
            assert len(classes_result["result"]["objectClasses"]) == 1

            mock_parallel.assert_awaited_once()
            mock_dedupe_classes.assert_awaited_once()

        # Step 2: Extract attributes
        with (
            patch("src.modules.digester.service.select_doc_chunks") as mock_chunks,
            patch("src.modules.digester.service._extract_rest_attributes") as mock_attrs,
            patch(
                "src.modules.digester.service.update_object_class_field_in_session",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_update_object_class,
            patch("src.modules.digester.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
        ):
            mock_chunks.return_value = (["chunk"], [(0, str(doc_uuid))])
            mock_attrs.return_value = {
                "result": {"attributes": {"id": {"type": "string", "description": "ID"}}},
                "relevantDocumentations": [],
            }

            attrs_result = await service.extract_attributes(
                doc_items, "User", session_id, [{"doc_id": str(doc_uuid), "chunk_id": str(doc_uuid)}], uuid4()
            )
            assert "id" in attrs_result["result"]["attributes"]
            mock_update_object_class.assert_awaited_once()

        # Step 3: Extract endpoints
        with (
            patch("src.modules.digester.service.select_doc_chunks") as mock_chunks,
            patch("src.modules.digester.service._extract_rest_endpoints") as mock_endpoints,
            patch(
                "src.modules.digester.service.update_object_class_field_in_session",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_update_object_class,
            patch("src.modules.digester.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
        ):
            mock_chunks.return_value = (["chunk"], [(0, str(doc_uuid))])
            mock_endpoints.return_value = {
                "result": {"endpoints": [{"method": "GET", "path": "/users", "description": "Get users"}]},
                "relevantDocumentations": [],
            }

            endpoints_result = await service.extract_endpoints(
                doc_items,
                "User",
                session_id,
                [{"doc_id": str(doc_uuid), "chunk_id": str(doc_uuid)}],
                uuid4(),
                "https://api.example.com",
            )
            assert len(endpoints_result["result"]["endpoints"]) == 1
            mock_update_object_class.assert_awaited_once()
