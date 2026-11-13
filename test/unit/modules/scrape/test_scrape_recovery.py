# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.jobs import job_input_reference
from src.jobs.payload import build_execution_payload, deserialize_call
from src.modules.scrape.schema import ScrapeJobInput
from src.modules.scrape.service import _run_scrape_async, fetch_relevant_documentation


class _AsyncSessionContext:
    def __init__(self, db: MagicMock):
        self.db = db

    async def __aenter__(self) -> MagicMock:
        return self.db

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


@pytest.mark.asyncio
async def test_reclaimed_scrape_reconstructs_result_from_rows_created_by_job() -> None:
    session_id = uuid4()
    job_id = uuid4()
    doc_id = uuid4()
    chunk_id = uuid4()
    persisted_chunk = {
        "chunkId": str(chunk_id),
        "docId": str(doc_id),
        "source": "scraper",
        "url": "https://example.test/docs",
        "summary": "Recovered summary",
        "content": "Chunk committed by the previous execution attempt.",
        "metadata": {"chunk_number": 0},
        "createdAt": "2026-07-29T10:00:00+00:00",
        "scrapeJobIds": [str(job_id)],
    }

    doc_repo = MagicMock()
    doc_repo.get_documentation_items_by_session = AsyncMock(return_value=[persisted_chunk])
    doc_repo.update_documentation_item = AsyncMock(return_value=True)
    doc_repo.get_scraped_documentation_items_for_export_by_job = AsyncMock(return_value=[persisted_chunk])

    db = MagicMock()
    db.commit = AsyncMock()
    fake_config = SimpleNamespace(
        scrape_and_process=SimpleNamespace(
            forbidden_url_parts=[],
            max_concurrent=1,
            max_scraper_iterations=1,
            chunk_length=1_000,
        ),
        jobs=SimpleNamespace(documentation_write_batch_size=20),
    )
    request = ScrapeJobInput(
        starter_links=["https://example.test/docs"],
        application_name="Example",
        skip_cache=True,
    )

    with (
        patch(
            "src.modules.scrape.service.async_session_maker",
            side_effect=lambda: _AsyncSessionContext(db),
        ),
        patch("src.modules.scrape.service.DocumentationRepository", return_value=doc_repo),
        patch("src.modules.scrape.service.config", fake_config),
        patch("src.modules.scrape.service.scraper_loop", new_callable=AsyncMock, return_value=[]),
        patch("src.modules.scrape.service.update_job_progress", new_callable=AsyncMock),
    ):
        result = await _run_scrape_async(request, job_id=job_id, session_id=session_id)

    assert result.saved_documentations_count == 1
    assert result.saved_chunks_count == 1
    assert len(result.saved_documentations) == 1
    assert result.saved_documentations[0].doc_id == doc_id
    assert result.saved_documentations[0].chunks[0].chunk_id == chunk_id
    doc_repo.get_scraped_documentation_items_for_export_by_job.assert_awaited_once_with(
        session_id,
        job_id,
    )


def test_scrape_job_queued_before_the_effective_version_still_runs() -> None:
    """A 1.0.x job input has no effectiveApplicationVersion; the worker accepts it and uses applicationVersion."""
    session_id = uuid4()
    old_input = {
        "starterLinks": ["https://example.test/docs"],
        "applicationName": "Example",
        "applicationVersion": "latest",
        "skipCache": False,
    }
    payload = build_execution_payload(
        worker=fetch_relevant_documentation,
        worker_args=(job_input_reference(), session_id),
        worker_kwargs={},
        dynamic_input_provider=None,
        session_result_key="scrapeOutput",
    )

    args, _ = deserialize_call(fetch_relevant_documentation, payload["args"], payload["kwargs"], job_input=old_input)

    assert args[0] == ScrapeJobInput.model_validate(old_input)
    assert args[0].effective_application_version is None


@pytest.mark.asyncio
async def test_documentation_reuse_compares_the_effective_version() -> None:
    session_id = uuid4()
    job_repo = MagicMock()
    job_repo.get_job_by_input = AsyncMock(return_value=None)
    doc_repo = MagicMock()
    doc_repo.get_documentation_items_by_session = AsyncMock(return_value=[])
    doc_repo.get_scraped_documentation_items_for_export_by_job = AsyncMock(return_value=[])
    db = MagicMock()
    db.commit = AsyncMock()
    request = ScrapeJobInput(
        starter_links=["https://docs.nextcloud.com/server/stable/"],
        application_name="Nextcloud",
        application_version="latest",
        effective_application_version="31",
    )

    with (
        patch("src.modules.scrape.service.async_session_maker", side_effect=lambda: _AsyncSessionContext(db)),
        patch("src.modules.scrape.service.JobRepository", return_value=job_repo),
        patch("src.modules.scrape.service.DocumentationRepository", return_value=doc_repo),
        patch("src.modules.scrape.service.scraper_loop", new_callable=AsyncMock, return_value=[]) as loop,
        patch("src.modules.scrape.service.update_job_progress", new_callable=AsyncMock),
    ):
        await _run_scrape_async(request, job_id=uuid4(), session_id=session_id)

    lookup, iteration = job_repo.get_job_by_input.await_args, loop.await_args
    assert lookup is not None and iteration is not None
    assert lookup.args[1]["effectiveApplicationVersion"] == "31"
    # The scrape targets the effective version, not the "latest" midPoint requested.
    assert iteration.args[1].version == "31"
