# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import ANY, AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.modules.discovery.router import discover_candidate_links
from src.modules.discovery.schema import CandidateLinksInput


@pytest.mark.asyncio
async def test_discover_candidate_links_success():
    """Test successful discovery of candidate links."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.discovery.router.SessionRepository", return_value=mock_repo),
        patch("src.modules.discovery.router.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        job_id = uuid4()
        session_id = uuid4()
        mock_schedule.return_value = job_id

        request = CandidateLinksInput(application_name="test-app")
        response = await discover_candidate_links(request, session_id, db=MagicMock())

        assert response.jobId == job_id
        mock_repo.session_exists.assert_awaited_once_with(session_id)
        mock_schedule.assert_awaited_once_with(
            job_type="discovery.getCandidateLinks",
            input_payload=request.model_dump(by_alias=True),
            worker=ANY,
            worker_args=(request, session_id),
            initial_stage="queue",
            initial_message="Queued candidate links discovery",
            session_id=session_id,
            session_result_key="discoveryOutput",
        )
        mock_repo.update_session.assert_awaited_once_with(
            session_id,
            {
                "discoveryJobId": str(job_id),
                "discoveryInput": request.model_dump(by_alias=True),
            },
        )


@pytest.mark.asyncio
async def test_discover_candidate_links_session_not_found():
    """Test discovery with non-existent session."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=False)

    with patch("src.modules.discovery.router.SessionRepository", return_value=mock_repo):
        request = CandidateLinksInput(application_name="test-app")
        with pytest.raises(HTTPException) as exc_info:
            await discover_candidate_links(request, uuid4(), db=MagicMock())

        assert exc_info.value.status_code == 404
        assert "not found" in str(exc_info.value.detail).lower()
