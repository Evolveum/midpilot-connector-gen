# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.modules.scrape.schema import ScrapeRequest
from src.modules.scrape.service import _run_scrape_async


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
    request = ScrapeRequest(
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
        patch("src.modules.scrape.service.get_base_domain", return_value="example.test"),
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
