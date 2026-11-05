# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import ANY, AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.jobs import job_input_reference
from src.modules.scrape.router import scrape_documentation
from src.modules.scrape.schema import ScrapeRequest


@pytest.mark.asyncio
async def test_scrape_documentation_uses_discovery_application_version_when_missing():
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.update_session = AsyncMock()
    mock_repo.get_session_data = AsyncMock(return_value={"applicationVersion": "LATEST"})

    with (
        patch("src.modules.scrape.router.SessionRepository", return_value=mock_repo),
        patch("src.modules.scrape.orchestration.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        job_id = uuid4()
        session_id = uuid4()
        mock_schedule.return_value = job_id
        request = ScrapeRequest(
            starter_links=["https://example.com/docs"],
            application_name="test-app",
        )
        expected_input = {
            **request.model_dump(by_alias=True),
            "applicationVersion": "LATEST",
            "effectiveApplicationVersion": "latest",
        }

        response = await scrape_documentation(request, session_id, db=MagicMock())

        assert response.jobId == job_id
        mock_repo.session_exists.assert_awaited_once_with(session_id)
        mock_repo.get_session_data.assert_awaited_once_with(session_id, "discoveryInput")
        mock_schedule.assert_awaited_once_with(
            db=mock_repo.db,
            job_type="scrape.getRelevantDocumentation",
            input_payload=expected_input,
            worker=ANY,
            worker_args=(job_input_reference(), session_id),
            initial_stage="queue",
            initial_message="Queued scraping job",
            session_id=session_id,
            session_result_key="scrapeOutput",
        )
        mock_repo.update_session.assert_awaited_once_with(
            session_id,
            {
                "scrapeJobId": str(job_id),
                "scrapeInput": expected_input,
            },
        )


@pytest.mark.asyncio
async def test_scrape_documentation_uses_default_application_version_without_discovery():
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.update_session = AsyncMock()
    mock_repo.get_session_data = AsyncMock(return_value={})

    with (
        patch("src.modules.scrape.router.SessionRepository", return_value=mock_repo),
        patch("src.modules.scrape.orchestration.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        job_id = uuid4()
        session_id = uuid4()
        mock_schedule.return_value = job_id
        request = ScrapeRequest(
            starter_links=["https://example.com/docs"],
            application_name="test-app",
        )

        expected_input = {**request.model_dump(by_alias=True), "effectiveApplicationVersion": "latest"}

        response = await scrape_documentation(request, session_id, db=MagicMock())

        assert response.jobId == job_id
        mock_repo.session_exists.assert_awaited_once_with(session_id)
        mock_repo.get_session_data.assert_awaited_once_with(session_id, "discoveryInput")
        mock_schedule.assert_awaited_once_with(
            db=mock_repo.db,
            job_type="scrape.getRelevantDocumentation",
            input_payload=expected_input,
            worker=ANY,
            worker_args=(job_input_reference(), session_id),
            initial_stage="queue",
            initial_message="Queued scraping job",
            session_id=session_id,
            session_result_key="scrapeOutput",
        )
        mock_repo.update_session.assert_awaited_once_with(
            session_id,
            {
                "scrapeJobId": str(job_id),
                "scrapeInput": expected_input,
            },
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("discovery_version", ["31", "35"])
async def test_scrape_requested_as_latest_targets_the_discovery_version(discovery_version: str):
    """midPoint always requests "latest"; the version picked in discovery decides what the scrape targets.

    The effective version is part of the job input and so of the cache key: scrapes of Nextcloud 31 and 35
    with the same starter links do not reuse each other's documentation.
    """
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.update_session = AsyncMock()
    mock_repo.get_session_data = AsyncMock(return_value={"applicationVersion": discovery_version})

    with (
        patch("src.modules.scrape.router.SessionRepository", return_value=mock_repo),
        patch("src.modules.scrape.orchestration.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        mock_schedule.return_value = uuid4()
        request = ScrapeRequest(
            starter_links=["https://docs.nextcloud.com/server/stable/developer_manual/"],
            application_name="Nextcloud",
            application_version="latest",
        )

        await scrape_documentation(request, uuid4(), db=MagicMock())

    assert mock_schedule.await_args is not None
    input_payload = mock_schedule.await_args.kwargs["input_payload"]
    assert input_payload["applicationVersion"] == "latest"
    assert input_payload["effectiveApplicationVersion"] == discovery_version
