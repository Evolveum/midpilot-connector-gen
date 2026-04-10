# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import ANY, AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.modules.scrape.router import scrape_documentation
from src.modules.scrape.schema import ScrapeRequest


@pytest.mark.asyncio
async def test_scrape_documentation_uses_explicit_application_version():
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.update_session = AsyncMock()
    mock_repo.get_session_data = AsyncMock()

    with (
        patch("src.modules.scrape.router.SessionRepository", return_value=mock_repo),
        patch("src.modules.scrape.router.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        job_id = uuid4()
        session_id = uuid4()
        mock_schedule.return_value = job_id
        request = ScrapeRequest(
            starter_links=["https://example.com/docs"],
            application_name="test-app",
            application_version="2.0",
        )

        response = await scrape_documentation(request, session_id, db=MagicMock())

        assert response.jobId == job_id
        mock_repo.session_exists.assert_awaited_once_with(session_id)
        mock_repo.get_session_data.assert_not_awaited()
        mock_schedule.assert_awaited_once_with(
            job_type="scrape.getRelevantDocumentation",
            input_payload=request.model_dump(by_alias=True),
            worker=ANY,
            worker_args=(request, session_id),
            initial_stage="queue",
            initial_message="Queued scraping job",
            session_id=session_id,
            session_result_key="scrapeOutput",
        )
        mock_repo.update_session.assert_awaited_once_with(
            session_id,
            {
                "scrapeJobId": str(job_id),
                "scrapeInput": request.model_dump(by_alias=True),
            },
        )
