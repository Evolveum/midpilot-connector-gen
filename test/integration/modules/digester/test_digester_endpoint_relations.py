# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Integration tests for digester relations endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.common.enums import JobStatus
from src.modules.digester.router import extract_relations, get_relations_status, override_relations
from src.modules.digester.schema import RelationsResponse


# RELATIONS
@pytest.mark.asyncio
async def test_extract_relations_success():
    """Test extracting relations, given that relevant object classes exist in session."""
    session_id = uuid4()
    job_id = uuid4()

    fake_docs = [{"docId": "page-1", "chunkId": "doc-1", "content": "fake content for testing"}]

    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.get_session_data = AsyncMock(return_value={"objectClasses": [{"name": "User", "relevant": "true"}]})
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.digester.router.SessionRepository", return_value=mock_repo),
        patch(
            "src.modules.digester.router.filter_documentation_items",
            new_callable=AsyncMock,
            return_value=fake_docs,
        ),
        patch("src.modules.digester.router.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        mock_schedule.return_value = job_id

        response = await extract_relations(
            session_id=session_id,
            db=MagicMock(),
        )

        assert response.jobId == job_id
        mock_repo.session_exists.assert_awaited_once_with(session_id)
        mock_repo.get_session_data.assert_awaited_once_with(session_id, "objectClassesOutput")
        mock_schedule.assert_awaited_once()
        mock_repo.update_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_relations_no_classes():
    """If there are no relevant object classes in the session, it should raise 404."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.get_session_data = AsyncMock(return_value=None)

    with (
        patch("src.modules.digester.router.SessionRepository", return_value=mock_repo),
        patch("src.modules.digester.router.filter_documentation_items", new_callable=AsyncMock, return_value=[]),
    ):
        session_id = uuid4()
        with pytest.raises(HTTPException) as exc_info:
            await extract_relations(session_id=session_id, db=MagicMock())

    assert exc_info.value.status_code == 404
    assert "no object classes" in exc_info.value.detail.lower()
    mock_repo.get_session_data.assert_awaited_once_with(session_id, "objectClassesOutput")


@pytest.mark.asyncio
async def test_get_relations_status_found():
    """Test getting relations extraction status."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    job_id = uuid4()
    mock_repo.get_session_data = AsyncMock(return_value=str(job_id))

    fake_status = MagicMock(
        jobId=job_id,
        status=JobStatus.finished,
        result=RelationsResponse(relations=[]),
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
        response = await get_relations_status(
            session_id=session_id,
            jobId=None,
            db=MagicMock(),
        )

    assert response.jobId == job_id
    assert response.status == JobStatus.finished
    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_repo.get_session_data.assert_awaited_once_with(session_id, "relationsJobId")
    mock_status_builder.assert_awaited_once()


@pytest.mark.asyncio
async def test_override_relations_success():
    """Test manual override of relations."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.update_session = AsyncMock()

    relations_payload = {"relations": [{"from": "User", "to": "Group", "type": "membership"}]}

    with patch("src.modules.digester.router.SessionRepository", return_value=mock_repo):
        session_id = uuid4()
        response = await override_relations(
            session_id=session_id,
            relations=relations_payload,
            db=MagicMock(),
        )

    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_repo.update_session.assert_awaited_once_with(session_id, {"relationsOutput": relations_payload})
    assert response["message"].startswith("Relations overridden successfully")
    assert response["sessionId"] == session_id
