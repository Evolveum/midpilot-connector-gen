# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Integration tests for digester metadata endpoints."""

from unittest.mock import ANY, AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.common.enums import JobStatus
from src.modules.digester import service
from src.modules.digester.router import extract_metadata, get_metadata_status, restore_metadata
from src.modules.digester.schema import InfoResponse


# METADATA
@pytest.mark.asyncio
async def test_extract_metadata_success():
    """Test extracting API metadata."""
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
        response = await extract_metadata(session_id=session_id, use_previous_session_data=True, db=MagicMock())

    assert response.jobId == job_id
    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_schedule.assert_awaited_once_with(
        job_type="digester.getInfoMetadata",
        input_payload={"usePreviousSessionData": True},
        dynamic_input_enabled=True,
        dynamic_input_provider=ANY,
        worker=service.extract_info_metadata,
        worker_kwargs={},
        initial_stage="chunking",
        initial_message="Preparing and splitting documentation",
        session_id=session_id,
        session_result_key="metadataOutput",
        await_documentation=True,
        await_documentation_timeout=750,
    )
    mock_repo.update_session.assert_awaited_once_with(
        session_id,
        {
            "metadataJobId": str(job_id),
            "metadataInput": {"usePreviousSessionData": True},
        },
    )


@pytest.mark.asyncio
async def test_get_metadata_status_found():
    """Test getting metadata extraction status."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    job_id = uuid4()
    mock_repo.get_session_data = AsyncMock(return_value=str(job_id))

    fake_status = MagicMock(jobId=job_id, status=JobStatus.finished, result=InfoResponse())

    with (
        patch("src.modules.digester.router.SessionRepository", return_value=mock_repo),
        patch(
            "src.modules.digester.router.build_typed_job_status_response",
            new_callable=AsyncMock,
            return_value=fake_status,
        ) as mock_status_builder,
    ):
        session_id = uuid4()
        response = await get_metadata_status(session_id=session_id, jobId=None, db=MagicMock())

    assert response.jobId == job_id
    assert response.status == JobStatus.finished
    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_repo.get_session_data.assert_awaited_once_with(session_id, "metadataJobId")
    mock_status_builder.assert_awaited_once()


@pytest.mark.asyncio
async def test_restore_metadata_success():
    """Test manual restore of metadata output."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.update_session = AsyncMock()

    payload = InfoResponse.model_validate(
        {
            "infoMetadata": {
                "name": "OpenProject",
                "apiType": ["REST"],
                "apiVersion": "3",
                "baseApiEndpoint": [{"uri": "https://example.com/api", "type": ""}],
                "applicationVersion": "12.1.0",
            }
        }
    )

    with patch("src.modules.digester.router.SessionRepository", return_value=mock_repo):
        session_id = uuid4()
        response = await restore_metadata(
            session_id=session_id,
            metadata=payload,
            db=MagicMock(),
        )

    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_repo.update_session.assert_awaited_once_with(
        session_id,
        {"metadataOutput": payload.model_dump(by_alias=True)},
    )
    assert response["message"].startswith("Metadata updated successfully")
    assert response["sessionId"] == session_id
