# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.modules.discovery.router import get_discovery_status


@pytest.mark.asyncio
async def test_get_discovery_status_found():
    """Test getting discovery status when job exists."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    job_id = uuid4()
    mock_repo.get_session_data = AsyncMock(return_value=str(job_id))

    fake_status = SimpleNamespace(jobId=job_id, status="completed", result={"candidateLinks": ["https://example.com"]})

    with (
        patch("src.modules.discovery.router.SessionRepository", return_value=mock_repo),
        patch(
            "src.modules.discovery.router.build_stage_status_response",
            new_callable=AsyncMock,
            return_value=fake_status,
        ) as mock_status,
    ):
        session_id = uuid4()
        response = await get_discovery_status(session_id, jobId=None, db=MagicMock())

        assert response.jobId == job_id
        assert response.status == "completed"
        assert "candidateLinks" in response.result
        mock_repo.session_exists.assert_awaited_once_with(session_id)
        mock_repo.get_session_data.assert_awaited_once_with(session_id, "discoveryJobId")
        mock_status.assert_awaited_once_with(job_id)


@pytest.mark.asyncio
async def test_get_discovery_status_not_found():
    """Test getting discovery status when no job exists."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.get_session_data = AsyncMock(return_value=None)

    with patch("src.modules.discovery.router.SessionRepository", return_value=mock_repo):
        session_id = uuid4()
        with pytest.raises(HTTPException) as exc_info:
            await get_discovery_status(session_id, jobId=None, db=MagicMock())

        assert exc_info.value.status_code == 404
        assert "no discovery job" in str(exc_info.value.detail).lower()
        mock_repo.session_exists.assert_awaited_once_with(session_id)
        mock_repo.get_session_data.assert_awaited_once_with(session_id, "discoveryJobId")
