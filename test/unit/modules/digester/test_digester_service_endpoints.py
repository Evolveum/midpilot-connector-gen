# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.modules.digester import service
from src.modules.digester.enums import EndpointMethod
from src.modules.digester.schema import EndpointInfo


# ==================== EXTRACT ENDPOINTS ====================
@pytest.mark.asyncio
async def test_extract_endpoints_updates_session_success(mock_llm, mock_digester_update_job_progress):
    """
    Test extract_endpoints successfully extracts endpoints and updates the session.
    Validates chunk selection, endpoint extraction, and session update.
    """
    session_id = uuid4()
    job_id = uuid4()
    doc_uuid = str(uuid4())
    base_api_url = "https://api.example.com"

    fake_doc_items = [
        {
            "uuid": doc_uuid,
            "content": "User endpoints documentation",
            "summary": "User API endpoints",
            "@metadata": {"source": "api_spec"},
        }
    ]

    relevant_chunks = [{"doc_id": doc_uuid, "chunk_id": doc_uuid}]

    with (
        patch("src.modules.digester.service.select_doc_chunks") as mock_extract_chunks,
        patch("src.modules.digester.service._extract_rest_endpoints") as mock_extract_endpoints,
        patch(
            "src.modules.digester.service.update_object_class_field_in_session",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_update_object_class,
        patch("src.modules.digester.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
    ):
        mock_extract_chunks.return_value = (["chunk-0 text"], [(0, doc_uuid)])

        mock_extract_endpoints.return_value = {
            "result": {
                "endpoints": [
                    EndpointInfo(
                        method=EndpointMethod.GET,
                        path="/users",
                        description="List all users",
                        suggested_use=["getAll"],
                    ).model_dump(),
                    EndpointInfo(
                        method=EndpointMethod.POST,
                        path="/users",
                        description="Create a new user",
                        suggested_use=["create"],
                    ).model_dump(),
                    EndpointInfo(
                        method=EndpointMethod.GET,
                        path="/users/{id}",
                        description="Get user by ID",
                        suggested_use=["getById"],
                    ).model_dump(),
                ]
            },
            "relevantDocumentations": relevant_chunks,
        }

        result = await service.extract_endpoints(
            fake_doc_items, "User", session_id, relevant_chunks, job_id, base_api_url
        )

        # Verify result structure
        assert "result" in result
        assert "endpoints" in result["result"]
        assert len(result["result"]["endpoints"]) == 3
        assert result["result"]["endpoints"][0]["path"] == "/users"
        assert result["result"]["endpoints"][0]["method"] == "GET"

        # Verify chunk extraction was called
        mock_extract_chunks.assert_called_once_with(fake_doc_items, relevant_chunks, "Digester:Endpoints")

        # Verify endpoint extraction was called with base_api_url
        mock_extract_endpoints.assert_called_once()
        call_args = mock_extract_endpoints.call_args
        assert call_args[0][3] == base_api_url
        mock_update_object_class.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_endpoints_no_relevant_chunks(mock_llm, mock_digester_update_job_progress):
    """Test extract_endpoints when no relevant chunks are found."""
    session_id = uuid4()
    job_id = uuid4()

    with (
        patch("src.modules.digester.service.select_doc_chunks") as mock_extract_chunks,
        patch("src.modules.digester.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
    ):
        mock_extract_chunks.return_value = ([], [])

        result = await service.extract_endpoints([], "User", session_id, [], job_id, "")

        assert result["result"]["endpoints"] == []
        assert result["relevantDocumentations"] == []


@pytest.mark.asyncio
async def test_extract_endpoints_with_base_url(mock_llm, mock_digester_update_job_progress):
    """Test extract_endpoints properly passes base_api_url to extraction function."""
    session_id = uuid4()
    job_id = uuid4()
    doc_uuid = str(uuid4())
    base_api_url = "https://custom-api.example.com/v2"

    fake_doc_items = [{"uuid": doc_uuid, "content": "test", "summary": "", "@metadata": {}}]
    relevant_chunks = [{"doc_id": doc_uuid, "chunk_id": doc_uuid}]

    with (
        patch("src.modules.digester.service.select_doc_chunks") as mock_extract_chunks,
        patch("src.modules.digester.service._extract_rest_endpoints") as mock_extract_endpoints,
        patch(
            "src.modules.digester.service.update_object_class_field_in_session",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_update_object_class,
        patch("src.modules.digester.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
    ):
        mock_extract_chunks.return_value = (["chunk"], [(0, doc_uuid)])
        mock_extract_endpoints.return_value = {"result": {"endpoints": []}, "relevantDocumentations": []}

        await service.extract_endpoints(fake_doc_items, "User", session_id, relevant_chunks, job_id, base_api_url)

        # Verify base_api_url was passed correctly
        call_args = mock_extract_endpoints.call_args
        assert call_args[0][3] == base_api_url
        mock_update_object_class.assert_awaited_once()
