# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Integration tests for digester auth endpoints."""

from unittest.mock import ANY, AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.common.enums import JobStatus
from src.modules.digester import service
from src.modules.digester.router import extract_auth, get_auth_status
from src.modules.digester.schema import AuthInfo, AuthResponse


# AUTH
@pytest.mark.asyncio
async def test_extract_auth_success():
    """Test extracting auth info."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.digester.router.SessionRepository", return_value=mock_repo),
        patch("src.modules.digester.router.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        job_id = uuid4()
        mock_schedule.return_value = job_id

        session_id = uuid4()
        response = await extract_auth(session_id=session_id, use_previous_session_data=True, db=MagicMock())

    assert response.jobId == job_id
    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_schedule.assert_awaited_once_with(
        job_type="digester.getAuth",
        input_payload={"usePreviousSessionData": True},
        dynamic_input_enabled=True,
        dynamic_input_provider=ANY,
        worker=service.extract_auth_with_fallback,
        worker_kwargs={},
        initial_stage="chunking",
        initial_message="Preparing and splitting documentation",
        session_id=session_id,
        session_result_key="authOutput",
        await_documentation=True,
        await_documentation_timeout=750,
    )
    mock_repo.update_session.assert_awaited_once_with(
        session_id,
        {
            "authJobId": str(job_id),
            "authInput": {"usePreviousSessionData": True},
        },
    )


@pytest.mark.asyncio
async def test_get_auth_status_found():
    """Test getting auth extraction status."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    job_id = uuid4()
    mock_repo.get_session_data = AsyncMock(return_value=str(job_id))

    fake_status = MagicMock(
        jobId=job_id,
        status=JobStatus.finished,
        result=AuthResponse(auth=[AuthInfo(name="OAuth2", type="oauth2")]),
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
        response = await get_auth_status(session_id=session_id, jobId=None, db=MagicMock())

    assert response.jobId == job_id
    assert response.status == JobStatus.finished
    assert len(response.result.auth) == 1
    assert response.result.auth[0].name == "OAuth2"
    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_repo.get_session_data.assert_awaited_once_with(session_id, "authJobId")
    mock_status_builder.assert_awaited_once()
