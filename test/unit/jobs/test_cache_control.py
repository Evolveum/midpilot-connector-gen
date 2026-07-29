# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.core.errors import JobClaimLostError
from src.jobs.cache import reuse_or_run
from src.modules.discovery.schema import CandidateLinksInput
from src.modules.scrape.schema import ScrapeRequest
from src.shared.normalize import normalize_input


class _AsyncSessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_normalize_input_ignores_skip_cache_for_job_identity() -> None:
    assert normalize_input({"applicationName": "Demo", "skipCache": True}) == {"applicationName": "Demo"}
    assert normalize_input({"applicationName": "Demo", "skipCache": False}) == {"applicationName": "Demo"}


def test_cache_control_defaults_to_reuse_for_request_models() -> None:
    discovery_input = CandidateLinksInput(application_name="Demo")
    scrape_input = ScrapeRequest(starter_links=["https://example.com/docs"], application_name="Demo")

    assert discovery_input.skip_cache is False
    assert discovery_input.model_dump(by_alias=True)["skipCache"] is False
    assert scrape_input.skip_cache is False
    assert scrape_input.model_dump(by_alias=True)["skipCache"] is False


def test_normalize_input_handles_missing_relevant_documentations() -> None:
    normalized = normalize_input(
        {
            "skipCache": True,
            "relevantObjectClasses": {
                "objectClasses": [
                    {"name": "User"},
                    {
                        "name": "Group",
                        "relevantDocumentations": [{"docId": "doc-1", "chunkId": "chunk-1"}],
                    },
                    {"name": "Role", "relevant_chunk_indices": [0, 1]},
                ]
            },
        }
    )

    assert "skipCache" not in normalized
    assert normalized["relevantObjectClasses"]["objectClasses"] == [
        {"name": "User"},
        {"name": "Group"},
        {"name": "Role"},
    ]


@pytest.mark.asyncio
async def test_cache_lookup_is_scoped_by_requesting_session() -> None:
    session_id = uuid4()
    job_repo = MagicMock()
    job_repo.get_job_by_input = AsyncMock(return_value=None)
    run_normal_worker = AsyncMock(return_value={"candidateLinks": []})
    db = MagicMock()
    db.rollback = AsyncMock()
    db.close = AsyncMock()

    with (
        patch("src.jobs.cache.async_session_maker", return_value=_AsyncSessionContext(db)),
        patch("src.jobs.cache.JobRepository", return_value=job_repo),
    ):
        result = await reuse_or_run(
            job_type="discovery.getCandidateLinks",
            job_id=uuid4(),
            session_id=session_id,
            input_payload={"applicationName": "Demo"},
            run_normal_worker=run_normal_worker,
        )

    assert result == {"candidateLinks": []}
    assert job_repo.get_job_by_input.await_args.kwargs == {"requesting_session_id": session_id}
    run_normal_worker.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_lost_claim_during_cache_reuse_never_runs_full_worker_again() -> None:
    session_id = uuid4()
    job_id = uuid4()
    latest_job = SimpleNamespace(
        job_id=uuid4(),
        session_id=uuid4(),
        result={"chunks_processed": 1},
        created_at=datetime.now(),
    )
    job_repo = MagicMock()
    job_repo.get_job_by_input = AsyncMock(return_value=latest_job)
    doc_repo = MagicMock()
    doc_repo.get_documentation_items_by_session_and_job = AsyncMock(
        return_value=[
            {
                "content": "chunk",
                "summary": "summary",
                "metadata": {},
            }
        ]
    )
    doc_repo.create_documentation_item = AsyncMock(side_effect=JobClaimLostError(job_id))
    db = MagicMock()
    db.commit = AsyncMock()
    run_normal_worker = AsyncMock(return_value={"should": "not run"})

    with (
        patch("src.jobs.cache.async_session_maker", return_value=_AsyncSessionContext(db)),
        patch("src.jobs.cache.JobRepository", return_value=job_repo),
        patch("src.jobs.cache.DocumentationRepository", return_value=doc_repo),
        patch("src.jobs.cache.lifecycle.update_job_progress", new_callable=AsyncMock),
    ):
        with pytest.raises(JobClaimLostError):
            await reuse_or_run(
                job_type="documentation.processUpload",
                job_id=job_id,
                session_id=session_id,
                input_payload={"doc_id": str(uuid4()), "filename": "doc.pdf"},
                run_normal_worker=run_normal_worker,
            )

    run_normal_worker.assert_not_awaited()
