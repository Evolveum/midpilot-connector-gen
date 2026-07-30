# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.config import config
from src.database.repositories.job_repository import ClaimedJob
from src.jobs import runner
from src.jobs.errors import JobClaimLostError
from src.jobs.lifecycle import increment_processed_documents
from src.jobs.payload import build_execution_payload
from src.jobs.runner import _resolve_dynamic_input, _run_claimed_job, schedule_coroutine_job
from src.jobs.session_persistence import persist_result_to_session


class _AsyncSessionContext:
    def __init__(self, session=None):
        self.session = session or MagicMock()
        self.session.commit = AsyncMock()
        self.session.rollback = AsyncMock()

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


async def durable_test_worker(value: str) -> dict[str, str]:
    return {"value": value}


@pytest.mark.asyncio
async def test_schedule_coroutine_job_persists_versioned_execution_payload_in_caller_transaction():
    job_id = uuid4()
    session_id = uuid4()
    db = MagicMock()
    repo = MagicMock()
    repo.create_job = AsyncMock(return_value=job_id)
    repo.update_job_progress = AsyncMock()

    with patch("src.jobs.runner.JobRepository", return_value=repo):
        returned_job_id = await schedule_coroutine_job(
            db=db,
            job_type="digester.test",
            input_payload={"skipCache": True},
            worker=durable_test_worker,
            worker_args=("hello",),
            initial_stage="queue",
            initial_message="Queued",
            session_id=session_id,
            session_result_key="testOutput",
        )

    assert returned_job_id == job_id
    create_kwargs = repo.create_job.await_args.kwargs
    execution_payload = create_kwargs["execution_payload"]
    assert execution_payload["version"] == 1
    assert execution_payload["worker"].endswith(":durable_test_worker")
    assert execution_payload["args"] == ["hello"]
    assert execution_payload["sessionResultKey"] == "testOutput"
    repo.update_job_progress.assert_awaited_once_with(job_id, stage="queue", message="Queued")


@pytest.mark.asyncio
async def test_documentation_dependency_is_persisted_without_starting_a_waiting_execution():
    job_id = uuid4()
    session_id = uuid4()
    repo = MagicMock()
    repo.create_job = AsyncMock(return_value=job_id)
    repo.update_job_progress = AsyncMock()

    with patch("src.jobs.runner.JobRepository", return_value=repo):
        await schedule_coroutine_job(
            db=MagicMock(),
            job_type="digester.test",
            input_payload={"skipCache": True},
            worker=durable_test_worker,
            worker_args=("hello",),
            initial_stage="chunking",
            initial_message="Preparing documentation",
            session_id=session_id,
            await_documentation=True,
            await_documentation_timeout=750,
        )

    create_kwargs = repo.create_job.await_args.kwargs
    assert create_kwargs["waits_for_documentation"] is True
    assert create_kwargs["documentation_wait_timeout_seconds"] == 750
    repo.update_job_progress.assert_awaited_once_with(
        job_id,
        stage="queue",
        message="Waiting for documentation processing to complete.",
    )


@pytest.mark.asyncio
async def test_dynamic_input_is_resolved_by_the_claiming_worker_and_persisted_under_its_fence():
    job_id = uuid4()
    session_id = uuid4()
    execution_token = uuid4()
    seen_payload: dict = {}

    async def provider(session_id, db, input_payload):
        seen_payload.update(input_payload)
        return {"sessionInput": {"count": 0}, "jobInput": {"documentationItems": []}, "args": ([],)}

    claimed = ClaimedJob(
        job_id=job_id,
        session_id=session_id,
        job_type="digester.test",
        input_payload={"skipCache": True, "apiType": "sql"},
        execution_payload={},
        worker_id="worker-a",
        execution_token=execution_token,
        attempt_count=1,
    )
    db = MagicMock()
    db.commit = AsyncMock()
    job_repo = MagicMock()
    job_repo.update_job_input = AsyncMock()
    session_repo = MagicMock()
    session_repo.update_session = AsyncMock()

    with (
        patch("src.jobs.runner.resolve_callable", return_value=provider),
        patch("src.jobs.runner.async_session_maker", return_value=_AsyncSessionContext(db)),
        patch("src.jobs.runner.JobRepository", return_value=job_repo),
        patch("src.jobs.runner.SessionRepository", return_value=session_repo),
    ):
        dynamic_input = await _resolve_dynamic_input(claimed, "test:provider", dict(claimed.input_payload))

    assert seen_payload == {"skipCache": True, "apiType": "sql"}
    assert dynamic_input["args"] == ([],)
    job_repo.update_job_input.assert_awaited_once_with(
        job_id,
        {"skipCache": True, "apiType": "sql", "documentationItems": []},
        worker_id="worker-a",
        execution_token=execution_token,
    )
    session_repo.update_session.assert_awaited_once_with(session_id, {"count": 0})
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_superseded_session_result_is_explicitly_recorded_before_job_finishes():
    job_id = uuid4()
    session_id = uuid4()
    claimed = ClaimedJob(
        job_id=job_id,
        session_id=session_id,
        job_type="digester.test",
        input_payload={"skipCache": True},
        execution_payload=build_execution_payload(
            worker=durable_test_worker,
            worker_args=("value",),
            worker_kwargs={},
            dynamic_input_provider=None,
            session_result_key="testOutput",
            await_documentation=False,
            await_documentation_timeout=None,
        ),
        worker_id="worker-a",
        execution_token=uuid4(),
        attempt_count=1,
    )
    job_repo = MagicMock()
    job_repo.get_job_artifacts = AsyncMock(return_value={})

    with (
        patch("src.jobs.runner.async_session_maker", return_value=_AsyncSessionContext()),
        patch("src.jobs.runner.JobRepository", return_value=job_repo),
        patch(
            "src.jobs.runner.session_persistence.persist_result_to_session",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch("src.jobs.runner.lifecycle.append_job_error", new_callable=AsyncMock) as append_error,
        patch("src.jobs.runner.lifecycle.set_finished", new_callable=AsyncMock) as set_finished,
    ):
        await _run_claimed_job(claimed)

    append_error.assert_awaited_once()
    assert "session points to a newer job" in append_error.await_args.args[1]
    set_finished.assert_awaited_once_with(job_id, result={"value": "value"})


@pytest.mark.asyncio
async def test_session_persistence_failure_is_recorded_and_fails_the_job_execution():
    job_id = uuid4()
    session_id = uuid4()
    repo = MagicMock()
    repo.update_result_if_current_job = AsyncMock(side_effect=RuntimeError("database write failed"))

    with (
        patch("src.jobs.session_persistence.async_session_maker", return_value=_AsyncSessionContext()),
        patch("src.jobs.session_persistence.SessionRepository", return_value=repo),
        patch("src.jobs.session_persistence.lifecycle._append_job_error_now", new_callable=AsyncMock) as append_error,
    ):
        with pytest.raises(RuntimeError, match="database write failed"):
            await persist_result_to_session(
                job_id=job_id,
                session_id=session_id,
                session_result_key="testOutput",
                result_dict={"value": "ok"},
                input_payload={},
            )

    append_error.assert_awaited_once()
    assert "Session persistence failed" in append_error.await_args.args[1]


@pytest.mark.asyncio
async def test_progress_increment_ignores_transient_db_error_but_propagates_lost_claim():
    repo = MagicMock()
    repo.increment_processed_documents = AsyncMock(side_effect=RuntimeError("temporary database error"))

    with (
        patch("src.jobs.lifecycle.async_session_maker", return_value=_AsyncSessionContext()),
        patch("src.jobs.lifecycle.JobRepository", return_value=repo),
    ):
        await increment_processed_documents(uuid4())

    job_id = uuid4()
    repo.increment_processed_documents = AsyncMock(side_effect=JobClaimLostError(job_id))
    with (
        patch("src.jobs.lifecycle.async_session_maker", return_value=_AsyncSessionContext()),
        patch("src.jobs.lifecycle.JobRepository", return_value=repo),
    ):
        with pytest.raises(JobClaimLostError):
            await increment_processed_documents(job_id)


@pytest.mark.asyncio
async def test_release_interrupted_claim_gives_up_when_the_database_does_not_answer(
    monkeypatch: pytest.MonkeyPatch,
):
    """Shutdown cancels executions, so releasing a claim must be time-boxed."""
    monkeypatch.setattr(config.jobs, "claim_release_timeout_seconds", 0.02)
    release_started = asyncio.Event()

    async def never_answers(claimed_job: ClaimedJob) -> None:
        release_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runner, "_release_claim", never_answers)
    claimed_job = ClaimedJob(
        job_id=uuid4(),
        session_id=uuid4(),
        job_type="digester.test",
        input_payload={},
        execution_payload={},
        worker_id="test-worker",
        execution_token=uuid4(),
        attempt_count=1,
    )

    # The bound is asserted by the timeout: an unbounded wait would hang here.
    await asyncio.wait_for(runner._release_interrupted_claim(claimed_job), timeout=5)

    assert release_started.is_set()
