# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Integration tests for digester connectivity endpoint routes."""

from unittest.mock import ANY, AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pytest

from src.common.enums import JobStatus
from src.modules.digester import service
from src.modules.digester.enums import EndpointMethod
from src.modules.digester.router import (
    extract_connectivity_endpoint,
    get_connectivity_endpoint_status,
    override_connectivity_endpoint,
)
from src.modules.digester.schemas import ConnectivityEndpointResponse


@pytest.mark.asyncio
async def test_extract_connectivity_endpoint_success():
    session_id = uuid4()
    job_id = uuid4()
    base_api_url = "https://api.example.com/api/v1/"

    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.digester.router.SessionRepository", return_value=mock_repo),
        patch(
            "src.modules.digester.router.get_session_base_api_url",
            new_callable=AsyncMock,
            return_value=base_api_url,
        ),
        patch("src.modules.digester.router.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        mock_schedule.return_value = job_id

        response = await extract_connectivity_endpoint(
            session_id=session_id,
            skip_cache=True,
            db=MagicMock(),
        )

    assert response.jobId == job_id
    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_schedule.assert_awaited_once_with(
        job_type="digester.getConnectivityEndpoint",
        input_payload={
            "baseApiUrl": base_api_url,
            "skipCache": True,
        },
        dynamic_input_enabled=True,
        dynamic_input_provider=ANY,
        worker=service.extract_connectivity_endpoint,
        worker_kwargs={
            "session_id": session_id,
            "base_api_url": base_api_url,
        },
        initial_stage="chunking",
        initial_message="Preparing documentation for connectivity endpoint extraction",
        session_id=session_id,
        session_result_key="connectivityEndpointOutput",
        await_documentation=True,
        await_documentation_timeout=750,
    )
    mock_repo.update_session.assert_awaited_once_with(
        session_id,
        {
            "connectivityEndpointJobId": str(job_id),
            "connectivityEndpointInput": {
                "baseApiUrl": base_api_url,
                "skipCache": True,
            },
        },
    )


@pytest.mark.asyncio
async def test_get_connectivity_endpoint_status_uses_session_output_when_finished():
    session_id = uuid4()
    job_id = uuid4()
    session_output = {
        "endpoints": [
            {
                "path": "/status",
                "method": "GET",
                "description": "Checks API status",
                "requiresAuth": True,
                "responseContentType": "application/json",
            },
            {
                "path": "/users",
                "method": "GET",
                "description": "List users",
                "requiresAuth": True,
            },
        ]
    }

    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.get_session_data = AsyncMock(side_effect=[str(job_id), session_output])
    mock_relevant_repo = MagicMock()
    mock_relevant_repo.get_relevant_chunks_grouped_by_entity = AsyncMock(
        return_value={
            "GET /status": [{"docId": "doc-1", "chunkId": "chunk-1"}],
            "GET /users": [],
        }
    )
    fake_status = MagicMock(jobId=job_id, status=JobStatus.finished, result=ConnectivityEndpointResponse())

    with (
        patch("src.modules.digester.router.SessionRepository", return_value=mock_repo),
        patch("src.common.utils.relevance.RelevantChunkRepository", return_value=mock_relevant_repo),
        patch(
            "src.modules.digester.router.build_typed_job_status_response",
            new_callable=AsyncMock,
            return_value=fake_status,
        ) as mock_status_builder,
    ):
        response = await get_connectivity_endpoint_status(session_id=session_id, jobId=None, db=MagicMock())

    assert response.jobId == job_id
    assert response.status == JobStatus.finished
    assert len(response.result.endpoints) == 2
    assert response.result.endpoints[0].path == "/status"
    assert response.result.endpoints[0].method == EndpointMethod.GET
    assert response.result.endpoints[0].requires_auth is True
    assert response.result.endpoints[0].relevant_documentations == [{"doc_id": "doc-1", "chunk_id": "chunk-1"}]
    assert response.result.endpoints[1].path == "/users"
    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_repo.get_session_data.assert_has_awaits(
        [
            call(session_id, "connectivityEndpointJobId"),
            call(session_id, "connectivityEndpointOutput"),
        ]
    )
    mock_status_builder.assert_awaited_once_with(job_id, ConnectivityEndpointResponse)


@pytest.mark.asyncio
async def test_override_connectivity_endpoint_success():
    session_id = uuid4()
    payload = ConnectivityEndpointResponse.model_validate(
        {
            "endpoints": [
                {
                    "path": "/ServiceProviderConfig",
                    "method": "GET",
                    "description": "SCIM service provider config",
                    "requiresAuth": True,
                    "relevantDocumentations": [{"docId": "doc-1", "chunkId": "chunk-1"}],
                }
            ]
        }
    )

    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.update_session = AsyncMock()
    mock_relevant_repo = MagicMock()
    mock_relevant_repo.replace_relevant_chunks_for_result = AsyncMock()

    with (
        patch("src.modules.digester.router.SessionRepository", return_value=mock_repo),
        patch("src.modules.digester.results.RelevantChunkRepository", return_value=mock_relevant_repo),
    ):
        response = await override_connectivity_endpoint(
            session_id=session_id,
            connectivity_endpoint=payload,
            db=MagicMock(),
        )

    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_repo.update_session.assert_awaited_once_with(
        session_id,
        {
            "connectivityEndpointOutput": {
                "endpoints": [
                    {
                        "path": "/ServiceProviderConfig",
                        "method": "GET",
                        "description": "SCIM service provider config",
                        "requiresAuth": True,
                        "responseContentType": None,
                        "requestContentType": None,
                    }
                ]
            }
        },
    )
    mock_relevant_repo.replace_relevant_chunks_for_result.assert_awaited_once()
    assert response["message"].startswith("Connectivity endpoint overridden successfully")
    assert response["sessionId"] == session_id
