# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.documents.errors import DocumentationUploadSupersededError
from src.documents.selection import DocumentationSelection, SelectionRole
from src.jobs.cache import reuse_or_run
from src.jobs.errors import JobClaimLostError
from src.modules.discovery.schema import CandidateLinksInput
from src.modules.scrape.schema import ScrapeRequest
from src.shared.job_types import JobCachePolicy, JobType, job_type_policy
from src.shared.normalize import normalize_input


def _cache_policy(job_type: JobType) -> JobCachePolicy:
    policy = job_type_policy(job_type).cache
    assert policy is not None
    return policy


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
            job_type=JobType.DISCOVERY_CANDIDATE_LINKS,
            cache_policy=_cache_policy(JobType.DISCOVERY_CANDIDATE_LINKS),
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
                job_type=JobType.DOCUMENTATION_PROCESS_UPLOAD,
                cache_policy=_cache_policy(JobType.DOCUMENTATION_PROCESS_UPLOAD),
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
                job_type=JobType.DOCUMENTATION_PROCESS_UPLOAD,
                cache_policy=_cache_policy(JobType.DOCUMENTATION_PROCESS_UPLOAD),
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
            job_type=JobType.CODEGEN_NATIVE_SCHEMA,
            cache_policy=_cache_policy(JobType.CODEGEN_NATIVE_SCHEMA),
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
            job_type=JobType.DOCUMENTATION_PROCESS_UPLOAD,
            cache_policy=_cache_policy(JobType.DOCUMENTATION_PROCESS_UPLOAD),
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


def _selection_item(content: str) -> dict:
    return {
        "chunkId": str(uuid4()),
        "docId": str(uuid4()),
        "url": "https://docs.example.com/users",
        "summary": content,
        "content": content,
        "@metadata": {"tags": ["user"]},
    }


def _attribute_selection(primary: dict, fallback: dict) -> DocumentationSelection:
    return DocumentationSelection.from_corpus(
        [primary, fallback], {SelectionRole.PRIMARY: [primary], SelectionRole.FALLBACK: [fallback]}
    )


def _attribute_output(primary: dict, fallback: dict) -> dict:
    return {
        "result": {
            "attributes": {
                "email": {
                    "type": "string",
                    "relevantDocumentations": [{"docId": fallback["docId"], "chunkId": fallback["chunkId"]}],
                }
            }
        },
        "relevantDocumentations": [{"doc_id": primary["docId"], "chunk_id": primary["chunkId"]}],
    }


async def _reuse_attribute_job(previous_input, latest_result, current_input):
    latest_job = SimpleNamespace(
        job_id=uuid4(), session_id=uuid4(), result=latest_result, errors=None, created_at=datetime.now()
    )
    job_repo = MagicMock()
    job_repo.get_job_by_input = AsyncMock(return_value=latest_job)
    job_repo.get_job_input = AsyncMock(return_value=previous_input)
    worker = AsyncMock(return_value={"fresh": True})
    with (
        patch("src.jobs.cache.async_session_maker", return_value=_AsyncSessionContext(MagicMock())),
        patch("src.jobs.cache.JobRepository", return_value=job_repo),
        patch("src.jobs.cache.DocumentationRepository") as doc_repo_cls,
        patch("src.jobs.cache.lifecycle.update_job_progress", new_callable=AsyncMock),
    ):
        result = await reuse_or_run(
            job_type=JobType.DIGESTER_ATTRIBUTES,
            cache_policy=_cache_policy(JobType.DIGESTER_ATTRIBUTES),
            job_id=uuid4(),
            session_id=uuid4(),
            input_payload=current_input,
            run_normal_worker=worker,
        )
    job_repo.get_job_input.assert_awaited_once_with(latest_job.job_id)
    # The source job's input captured what it read: no session documentation is consulted.
    doc_repo_cls.assert_not_called()
    return result, worker


@pytest.mark.asyncio
async def test_selection_reuse_remaps_relevance_between_the_stored_inputs():
    previous_primary, previous_fallback = _selection_item("User overview"), _selection_item("User reference")
    current_primary = {**previous_primary, "chunkId": str(uuid4()), "docId": str(uuid4())}
    current_fallback = {**previous_fallback, "chunkId": str(uuid4()), "docId": str(uuid4())}
    previous_input = {
        "documentationSelection": _attribute_selection(previous_primary, previous_fallback).to_job_input()
    }
    current_input = {"documentationSelection": _attribute_selection(current_primary, current_fallback).to_job_input()}

    result, worker = await _reuse_attribute_job(
        previous_input, _attribute_output(previous_primary, previous_fallback), current_input
    )

    worker.assert_not_awaited()
    assert result["relevantDocumentations"] == [
        {"doc_id": current_primary["docId"], "chunk_id": current_primary["chunkId"]}
    ]
    assert result["result"]["attributes"]["email"]["relevantDocumentations"] == [
        {"docId": current_fallback["docId"], "chunkId": current_fallback["chunkId"]}
    ]


@pytest.mark.asyncio
async def test_selection_reuse_never_publishes_references_outside_the_source_selection():
    primary, fallback = _selection_item("User overview"), _selection_item("User reference")
    selection_input = {"documentationSelection": _attribute_selection(primary, fallback).to_job_input()}
    cached = _attribute_output(primary, fallback)
    cached["relevantDocumentations"].append({"doc_id": str(uuid4()), "chunk_id": str(uuid4())})

    result, worker = await _reuse_attribute_job(selection_input, cached, selection_input)

    assert result == {"fresh": True}
    worker.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_selection_reuse_runs_the_worker_when_the_source_input_is_not_a_valid_selection():
    primary, fallback = _selection_item("User overview"), _selection_item("User reference")
    current_input = {"documentationSelection": _attribute_selection(primary, fallback).to_job_input()}

    result, worker = await _reuse_attribute_job(
        {"documentationItems": []}, _attribute_output(primary, fallback), current_input
    )

    assert result == {"fresh": True}
    worker.assert_awaited_once_with()
