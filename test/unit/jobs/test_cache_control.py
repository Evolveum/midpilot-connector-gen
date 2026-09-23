# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.documents.errors import DocumentationUploadSupersededError
from src.jobs.cache import reuse_or_run
from src.jobs.errors import JobClaimLostError
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
@pytest.mark.parametrize("superseded", [False, True])
async def test_lost_ownership_during_cache_reuse_never_runs_full_worker_again(superseded) -> None:
    session_id = uuid4()
    job_id = uuid4()
    error = DocumentationUploadSupersededError() if superseded else JobClaimLostError(job_id)
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
    db = MagicMock()
    db.commit = AsyncMock()
    run_normal_worker = AsyncMock(return_value={"should": "not run"})

    with (
        patch("src.jobs.cache.async_session_maker", return_value=_AsyncSessionContext(db)),
        patch("src.jobs.cache.JobRepository", return_value=job_repo),
        patch("src.jobs.cache.DocumentationRepository", return_value=doc_repo),
        patch("src.jobs.cache.lifecycle.update_job_progress", new_callable=AsyncMock),
        patch(
            "src.jobs.cache.publish_uploaded_documentation",
            new_callable=AsyncMock,
            side_effect=error,
        ),
    ):
        with pytest.raises(type(error)):
            await reuse_or_run(
                job_type="documentation.processUpload",
                job_id=job_id,
                session_id=session_id,
                input_payload={"doc_id": str(uuid4()), "filename": "doc.pdf"},
                run_normal_worker=run_normal_worker,
            )

    run_normal_worker.assert_not_awaited()


@pytest.mark.asyncio
async def test_unexpected_cache_reuse_failure_does_not_trigger_expensive_worker() -> None:
    latest_job = SimpleNamespace(
        job_id=uuid4(),
        session_id=uuid4(),
        result={"chunks_processed": 1},
        created_at=datetime.now(),
    )
    job_repo = MagicMock()
    job_repo.get_job_by_input = AsyncMock(return_value=latest_job)
    doc_repo = MagicMock()
    doc_repo.get_documentation_items_by_session_and_job = AsyncMock(side_effect=RuntimeError("database unavailable"))
    db = MagicMock()
    run_normal_worker = AsyncMock(return_value={"should": "not run"})

    with (
        patch("src.jobs.cache.async_session_maker", return_value=_AsyncSessionContext(db)),
        patch("src.jobs.cache.JobRepository", return_value=job_repo),
        patch("src.jobs.cache.DocumentationRepository", return_value=doc_repo),
        patch("src.jobs.cache.lifecycle.update_job_progress", new_callable=AsyncMock),
    ):
        with pytest.raises(RuntimeError, match="database unavailable"):
            await reuse_or_run(
                job_type="documentation.processUpload",
                job_id=uuid4(),
                session_id=uuid4(),
                input_payload={"doc_id": str(uuid4()), "filename": "doc.pdf"},
                run_normal_worker=run_normal_worker,
            )

    run_normal_worker.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("code", "code_format"), [("", None), ("{}", "YAML"), ("search {}", "GROOVY")])
async def test_codegen_cache_preserves_result_and_diagnostics(code, code_format):
    from src.modules.codegen.repair import NO_CODE_GENERATED

    diagnostics = [NO_CODE_GENERATED] if not code else ["A chunk failed validation"]
    latest_job = SimpleNamespace(
        job_id=uuid4(),
        session_id=uuid4(),
        result={"format": code_format, "code": code},
        errors=diagnostics,
        created_at=datetime.now(),
    )
    repo = MagicMock()
    repo.get_job_by_input = AsyncMock(return_value=latest_job)
    worker = AsyncMock()
    job_id = uuid4()
    with (
        patch("src.jobs.cache.async_session_maker", return_value=_AsyncSessionContext(MagicMock())),
        patch("src.jobs.cache.JobRepository", return_value=repo),
        patch("src.jobs.cache.lifecycle.update_job_progress", new_callable=AsyncMock),
        patch("src.jobs.cache.lifecycle.append_job_error", new_callable=AsyncMock) as errors,
    ):
        result = await reuse_or_run(
            job_type="codegen.getNativeSchema",
            job_id=job_id,
            session_id=uuid4(),
            input_payload={},
            run_normal_worker=worker,
        )
    assert result == {"format": code_format, "code": code}
    assert result is not latest_job.result
    errors.assert_awaited_once_with(job_id, diagnostics[0])
    worker.assert_not_awaited()


@pytest.mark.asyncio
async def test_cached_upload_publishes_complete_document_and_updates_result_identity():
    source_doc_id, target_doc_id, job_id, session_id = (uuid4() for _ in range(4))
    latest_job = SimpleNamespace(
        job_id=uuid4(),
        session_id=uuid4(),
        created_at=datetime.now(),
        result={
            "chunks_processed": 2,
            "doc_id": str(source_doc_id),
            "filename": "old.md",
            "content_type": "text/markdown",
        },
    )
    job_repo = MagicMock()
    job_repo.get_job_by_input = AsyncMock(return_value=latest_job)
    items = [
        {
            "content": f"chunk-{i}",
            "summary": "summary",
            "metadata": {"chunk_number": i, "filename": "old.md", "parser": "text"},
        }
        for i in range(2)
    ]
    doc_repo = MagicMock()
    doc_repo.get_documentation_items_by_session_and_job = AsyncMock(return_value=items)
    worker = AsyncMock()
    with (
        patch("src.jobs.cache.async_session_maker", return_value=_AsyncSessionContext(MagicMock())),
        patch("src.jobs.cache.JobRepository", return_value=job_repo),
        patch("src.jobs.cache.DocumentationRepository", return_value=doc_repo),
        patch("src.jobs.cache.lifecycle.update_job_progress", new_callable=AsyncMock),
        patch("src.jobs.cache.publish_uploaded_documentation", new_callable=AsyncMock) as publish,
    ):
        result = await reuse_or_run(
            job_type="documentation.processUpload",
            job_id=job_id,
            session_id=session_id,
            input_payload={"doc_id": str(target_doc_id), "filename": "new.md"},
            run_normal_worker=worker,
        )
    publish.assert_awaited_once_with(
        session_id=session_id,
        doc_id=target_doc_id,
        job_id=job_id,
        filename="new.md",
        chunks=[{**item, "metadata": {**item["metadata"], "filename": "new.md"}} for item in items],
    )
    assert result == {**latest_job.result, "doc_id": str(target_doc_id), "filename": "new.md"}
    assert latest_job.result["doc_id"] == str(source_doc_id)
    worker.assert_not_awaited()
