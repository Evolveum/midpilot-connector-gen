# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Integration tests for digester object-class endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.common.enums import JobStatus
from src.modules.digester.router import extract_object_classes, get_object_classes_status

# CLASSES (object classes)


@pytest.mark.asyncio
async def test_extract_object_classes_success():
    """Test successful extraction of object classes."""
    session_id = uuid4()
    job_id = uuid4()

    fake_docs = [{"docId": str(uuid4()), "chunkId": str(uuid4()), "content": "fake content for testing"}]

    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.digester.router.SessionRepository", return_value=mock_repo),
        patch("src.modules.digester.router.get_session_documentation", new_callable=AsyncMock, return_value=fake_docs),
        patch("src.modules.digester.router.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        mock_schedule.return_value = job_id

        response = await extract_object_classes(session_id, db=MagicMock())

        assert response.jobId == job_id
        mock_repo.session_exists.assert_awaited_once_with(session_id)
        mock_schedule.assert_awaited_once()
        mock_repo.update_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_object_classes_session_not_found():
    """Test extraction with non-existent session."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=False)

    with patch("src.modules.digester.router.SessionRepository", return_value=mock_repo):
        with pytest.raises(HTTPException) as exc_info:
            await extract_object_classes(uuid4(), db=MagicMock())

        assert exc_info.value.status_code == 404
        assert "not found" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_get_object_classes_status_found():
    """Test getting object classes status when job exists."""
    mock_repo = MagicMock()
    job_id = uuid4()

    object_classes_output = {
        "objectClasses": [
            {
                "name": "User",
                "relevant": "true",
                "description": "User's description",
                "relevantDocumentations": [],
            },
            {
                "name": "Group",
                "relevant": "true",
                "description": "Group's description",
                "relevantDocumentations": [],
            },
        ]
    }

    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.get_session_data = AsyncMock(side_effect=[str(job_id), object_classes_output])

    mock_status = {
        "jobId": job_id,
        "status": "finished",
        "result": {
            "objectClasses": [
                {"name": "User", "relevant": "true", "description": "User's description"},
                {"name": "Group", "relevant": "true", "description": "Group's description"},
            ]
        },
    }

    with (
        patch("src.modules.digester.router.SessionRepository", return_value=mock_repo),
        patch("src.common.utils.status_response.get_job_status", new_callable=AsyncMock, return_value=mock_status),
    ):
        session_id = uuid4()
        response = await get_object_classes_status(session_id, db=MagicMock())

    assert response.jobId == job_id
    assert response.status == JobStatus.finished
    assert len(response.result.objectClasses) == 2
    assert response.result.objectClasses[0].name == "User"
    assert response.result.objectClasses[1].name == "Group"
