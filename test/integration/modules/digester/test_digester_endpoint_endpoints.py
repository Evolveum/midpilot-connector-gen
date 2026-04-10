# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Integration tests for digester class-endpoints endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.common.enums import JobStatus
from src.modules.digester.router import (
    extract_class_endpoints,
    get_class_endpoints_status,
    override_class_endpoints,
)
from src.modules.digester.schema import EndpointInfo, EndpointResponse


# CLASS ENDPOINTS
@pytest.mark.asyncio
async def test_extract_class_endpoints_success():
    """Test successful extraction of endpoints for object class."""
    session_id = uuid4()
    job_id = uuid4()

    fake_docs = [{"docId": "page-1", "chunkId": "doc-1", "content": "fake content for testing"}]

    # Mock objectClassesOutput with relevant chunks for the User class
    mock_object_classes_output = {
        "objectClasses": [
            {
                "name": "User",
                "relevant": "true",
                "superclass": "",
                "abstract": False,
                "embedded": False,
                "description": "Represents a user",
                "relevantDocumentations": [
                    {"docId": "page-1", "chunkId": "doc-1"},
                    {"docId": "page-1", "chunkId": "doc-1"},
                ],
                "endpoints": [],
            }
        ]
    }

    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.get_session_data = AsyncMock(return_value=mock_object_classes_output)
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.digester.router.SessionRepository", return_value=mock_repo),
        patch("src.modules.digester.router.get_session_api_types", new_callable=AsyncMock, return_value=[]),
        patch(
            "src.modules.digester.router.get_session_base_api_url",
            new_callable=AsyncMock,
            return_value="https://api.example.com",
        ),
        patch(
            "src.modules.digester.router.filter_documentation_items",
            new_callable=AsyncMock,
            return_value=[{"docId": "page-1", "chunkId": "doc-1"}],
        ),
        patch(
            "src.modules.digester.router.get_session_documentation",
            new=AsyncMock(return_value=fake_docs),
        ),
        patch("src.modules.digester.router.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        mock_schedule.return_value = job_id

        response = await extract_class_endpoints(
            session_id=session_id,
            object_class="User",
            db=MagicMock(),
        )

        assert response.jobId == job_id
        mock_repo.session_exists.assert_awaited_once_with(session_id)
        mock_schedule.assert_awaited_once()
        mock_repo.update_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_class_endpoints_status_found():
    """Test getting endpoints extraction status when job exists."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    job_id = uuid4()
    mock_repo.get_session_data = AsyncMock(return_value=str(job_id))

    fake_status = MagicMock(
        jobId=job_id,
        status=JobStatus.finished,
        result=EndpointResponse(endpoints=[EndpointInfo(method="GET", path="/users", description="List users")]),
    )

    with (
        patch("src.modules.digester.router.SessionRepository", return_value=mock_repo),
        patch(
            "src.modules.digester.router.build_typed_job_status_response",
            new_callable=AsyncMock,
            return_value=fake_status,
        ) as mock_status_builder,
    ):
        session_id = uuid4()
        response = await get_class_endpoints_status(
            session_id=session_id,
            object_class="User",
            jobId=None,
            db=MagicMock(),
        )

    assert response.jobId == job_id
    assert response.status == JobStatus.finished
    assert len(response.result.endpoints) == 1
    assert response.result.endpoints[0].method == "GET"
    assert response.result.endpoints[0].path == "/users"
    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_repo.get_session_data.assert_awaited_once_with(session_id, "UserEndpointsJobId")
    mock_status_builder.assert_awaited_once()


@pytest.mark.asyncio
async def test_override_class_endpoints_success():
    """Test manual override of endpoints."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.update_session = AsyncMock()

    with patch("src.modules.digester.router.SessionRepository", return_value=mock_repo):
        session_id = uuid4()
        response = await override_class_endpoints(
            session_id=session_id,
            object_class="User",
            endpoints={"listUsers": {"method": "GET", "path": "/users"}},
            db=MagicMock(),
        )

    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_repo.update_session.assert_awaited_once_with(
        session_id,
        {"userEndpointsOutput": {"listUsers": {"method": "GET", "path": "/users"}}},
    )
    assert response["message"].startswith("Endpoints for user overridden successfully")
    assert response["sessionId"] == session_id
    assert response["objectClass"] == "user"
