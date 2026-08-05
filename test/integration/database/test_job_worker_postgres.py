# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.errors import LLMUnavailableError
from src.core.job_execution import (
    JobExecutionContext,
    reset_current_execution,
    set_current_execution,
)
from src.database.models import Base, DocumentationChunk, Job, JobProgress, Session
from src.database.repositories.documentation_repository import DocumentationRepository
from src.database.repositories.job_repository import ClaimedJob, JobRepository
from src.database.repositories.session_repository import SessionRepository
from src.documents.errors import NoDocumentationStoredError
from src.jobs.payload import build_execution_payload
from src.jobs.runner import execute_claimed_job
from src.shared.enums import JobStage
from src.shared.normalize import normalized_input_fingerprint

SessionFactory = async_sessionmaker[AsyncSession]


async def durable_echo_worker(value: str, *, job_id: UUID) -> dict[str, str]:
    return {"value": value, "jobId": str(job_id)}


async def durable_missing_documentation_worker(session_id: UUID) -> dict[str, str]:
    raise NoDocumentationStoredError(session_id)


async def durable_unavailable_llm_worker() -> dict[str, str]:
    try:
        raise TimeoutError("provider timed out")
    except TimeoutError as exc:
        raise LLMUnavailableError("generating a connector") from exc


def _execution_payload(value: str) -> dict:
    return build_execution_payload(
        worker=durable_echo_worker,
        worker_args=(value,),
        worker_kwargs={},
        dynamic_input_provider=None,
        session_result_key=None,
        await_documentation=False,
        await_documentation_timeout=None,
    )


@pytest_asyncio.fixture
async def postgres_session_factory() -> AsyncIterator[SessionFactory]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL repository integration tests")

    schema_name = f"test_job_worker_{uuid4().hex}"
    engine = create_async_engine(
        database_url,
        execution_options={"schema_translate_map": {None: schema_name}},
    )
    schema_created = False
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
            schema_created = True
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        if schema_created:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        await engine.dispose()


async def _create_session(session_factory: SessionFactory) -> UUID:
    session_id = uuid4()
    async with session_factory() as db:
        db.add(Session(session_id=session_id))
        await db.commit()
    return session_id


async def _create_queued_job(
    session_factory: SessionFactory,
    session_id: UUID,
    *,
    value: str = "test",
) -> UUID:
    job_id = uuid4()
    async with session_factory() as db:
        db.add(
            Job(
                job_id=job_id,
                session_id=session_id,
                job_type="test.worker",
                status="queued",
                input={"value": value},
                normalized_input_hash=normalized_input_fingerprint({"value": value}),
                execution_payload=_execution_payload(value),
                max_attempts=3,
            )
        )
        await db.commit()
    return job_id


async def _claim(
    session_factory: SessionFactory,
    *,
    worker_id: str,
) -> ClaimedJob | None:
    async with session_factory() as db:
        claimed = await JobRepository(db).claim_next_job(
            worker_id=worker_id,
            claim_timeout_seconds=60,
        )
        await db.commit()
        return claimed


@pytest.mark.asyncio
async def test_concurrent_claim_and_stale_finalization_are_fenced(
    postgres_session_factory: SessionFactory,
) -> None:
    session_id = await _create_session(postgres_session_factory)
    job_id = await _create_queued_job(postgres_session_factory, session_id)

    first, second = await asyncio.gather(
        _claim(postgres_session_factory, worker_id="worker-a"),
        _claim(postgres_session_factory, worker_id="worker-b"),
    )
    claims = [claim for claim in (first, second) if claim is not None]
    assert len(claims) == 1
    first_claim = claims[0]

    async with postgres_session_factory() as db:
        assert await JobRepository(db).fail_expired_exhausted_jobs() == 0
        active_job = await JobRepository(db).get_job(job_id)
        await db.commit()
    assert active_job is not None
    assert active_job.status == "running"
    assert active_job.execution_token == first_claim.execution_token

    async with postgres_session_factory() as db:
        await db.execute(
            update(Job)
            .where(Job.job_id == job_id)
            .values(claim_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
        await db.commit()

    second_claim = await _claim(postgres_session_factory, worker_id="worker-c")
    assert second_claim is not None
    assert second_claim.execution_token != first_claim.execution_token
    assert second_claim.attempt_count == 2

    async with postgres_session_factory() as db:
        stale_result = await JobRepository(db).finish_claimed_job(
            job_id,
            {"winner": "stale"},
            worker_id=first_claim.worker_id,
            execution_token=first_claim.execution_token,
        )
        winning_result = await JobRepository(db).finish_claimed_job(
            job_id,
            {"winner": "worker-c"},
            worker_id=second_claim.worker_id,
            execution_token=second_claim.execution_token,
        )
        await db.commit()

    assert stale_result is None
    assert winning_result is not None
    async with postgres_session_factory() as db:
        job = await JobRepository(db).get_job(job_id)
    assert job is not None
    assert job.status == "finished"
    assert job.result == {"winner": "worker-c"}
    assert job.worker_id is None
    assert job.execution_token is None


@pytest.mark.asyncio
async def test_stale_job_cannot_overwrite_newer_session_pointer(
    postgres_session_factory: SessionFactory,
) -> None:
    session_id = await _create_session(postgres_session_factory)
    job_id = await _create_queued_job(postgres_session_factory, session_id)

    async with postgres_session_factory() as db:
        session_repo = SessionRepository(db)
        await session_repo.update_session(session_id, {"testJobId": str(uuid4())})
        await db.commit()
    async with postgres_session_factory() as db:
        persisted = await SessionRepository(db).update_result_if_current_job(
            session_id=session_id,
            result_key="testOutput",
            job_id=job_id,
            value={"winner": "stale"},
        )
        await db.commit()

    assert persisted is False


@pytest.mark.asyncio
async def test_documentation_retry_is_idempotent_and_preserves_other_job_links(
    postgres_session_factory: SessionFactory,
) -> None:
    session_id = await _create_session(postgres_session_factory)
    job_id = await _create_queued_job(postgres_session_factory, session_id)

    async with postgres_session_factory() as db:
        doc_repo = DocumentationRepository(db)
        first_chunk_id = await doc_repo.create_documentation_item(
            session_id,
            "upload",
            "same content",
            original_job_id=job_id,
            doc_id=uuid4(),
            url="upload://same.txt",
            metadata={"chunk_number": 0},
        )
        second_chunk_id = await doc_repo.create_documentation_item(
            session_id,
            "upload",
            "same content",
            original_job_id=job_id,
            doc_id=uuid4(),
            url="upload://same.txt",
            metadata={"chunk_number": 0},
        )
        count = (
            await db.execute(
                select(func.count()).select_from(DocumentationChunk).where(DocumentationChunk.origin_job_id == job_id)
            )
        ).scalar_one()
        await db.commit()

    assert first_chunk_id == second_chunk_id
    assert count == 1

    linked_job_id = uuid4()
    async with postgres_session_factory() as db:
        doc_repo = DocumentationRepository(db)
        assert await doc_repo.update_documentation_item(first_chunk_id, original_job_id=linked_job_id)
        await doc_repo.create_documentation_item(
            session_id,
            "upload",
            "same content",
            original_job_id=job_id,
            doc_id=uuid4(),
            url="upload://same.txt",
            metadata={"chunk_number": 0},
        )
        linked_item = (
            await db.execute(select(DocumentationChunk).where(DocumentationChunk.chunk_id == first_chunk_id))
        ).scalar_one()
        await db.commit()

    assert set(linked_item.scrape_job_ids) == {str(job_id), str(linked_job_id)}


@pytest.mark.asyncio
async def test_documentation_dependency_does_not_consume_an_execution_attempt(
    postgres_session_factory: SessionFactory,
) -> None:
    session_id = await _create_session(postgres_session_factory)
    async with postgres_session_factory() as db:
        repo = JobRepository(db)
        dependent_job_id = await repo.create_job(
            {"value": "dependent"},
            "digester.getAuth",
            session_id,
            execution_payload=_execution_payload("dependent"),
            documentation_wait_timeout_seconds=750,
        )
        producer_job_id = await repo.create_job(
            {"value": "producer"},
            "documentation.processUpload",
            session_id,
            execution_payload=_execution_payload("producer"),
        )
        await db.commit()

    producer_claim = await _claim(postgres_session_factory, worker_id="worker-producer")
    assert producer_claim is not None
    assert producer_claim.job_id == producer_job_id

    async with postgres_session_factory() as db:
        dependent_before = await JobRepository(db).get_job(dependent_job_id)
        assert dependent_before is not None
        assert dependent_before.status == "queued"
        assert dependent_before.attempt_count == 0
        await JobRepository(db).finish_claimed_job(
            producer_job_id,
            {"value": "producer"},
            worker_id=producer_claim.worker_id,
            execution_token=producer_claim.execution_token,
        )
        await db.commit()

    dependent_claim = await _claim(postgres_session_factory, worker_id="worker-dependent")
    assert dependent_claim is not None
    assert dependent_claim.job_id == dependent_job_id


@pytest.mark.asyncio
async def test_documentation_wait_timeout_is_recorded_when_producer_remains_pending(
    postgres_session_factory: SessionFactory,
) -> None:
    session_id = await _create_session(postgres_session_factory)
    async with postgres_session_factory() as db:
        repo = JobRepository(db)
        dependent_job_id = await repo.create_job(
            {"value": "dependent"},
            "digester.getAuth",
            session_id,
            execution_payload=_execution_payload("dependent"),
            documentation_wait_timeout_seconds=750,
        )
        await repo.create_job(
            {"value": "producer"},
            "documentation.processUpload",
            session_id,
            execution_payload=_execution_payload("producer"),
        )
        await db.execute(
            update(Job)
            .where(Job.job_id == dependent_job_id)
            .values(documentation_wait_until=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
        await db.commit()

    dependent_claim = await _claim(postgres_session_factory, worker_id="worker-dependent")
    assert dependent_claim is not None
    assert dependent_claim.job_id == dependent_job_id
    async with postgres_session_factory() as db:
        dependent_job = await JobRepository(db).get_job(dependent_job_id)
    assert dependent_job is not None
    assert dependent_job.errors is not None
    assert any("Timed out waiting for documentation processing" in error for error in dependent_job.errors)


@pytest.mark.asyncio
async def test_documentation_fence_does_not_block_heartbeat(
    postgres_session_factory: SessionFactory,
) -> None:
    session_id = await _create_session(postgres_session_factory)
    job_id = await _create_queued_job(postgres_session_factory, session_id)
    claim = await _claim(postgres_session_factory, worker_id="worker-a")
    assert claim is not None

    context_token = set_current_execution(
        JobExecutionContext(
            job_id=claim.job_id,
            session_id=claim.session_id,
            job_type=claim.job_type,
            worker_id=claim.worker_id,
            execution_token=claim.execution_token,
        )
    )
    try:
        async with postgres_session_factory() as documentation_db:
            await DocumentationRepository(documentation_db).create_documentation_item(
                session_id,
                "upload",
                "heartbeat-safe content",
                original_job_id=job_id,
                doc_id=uuid4(),
                url="upload://heartbeat-safe.txt",
                metadata={"chunk_number": 0},
            )

            async def refresh_while_documentation_transaction_is_open() -> bool:
                async with postgres_session_factory() as heartbeat_db:
                    refreshed = await JobRepository(heartbeat_db).refresh_claim(
                        job_id,
                        worker_id=claim.worker_id,
                        execution_token=claim.execution_token,
                        claim_timeout_seconds=60,
                    )
                    await heartbeat_db.commit()
                    return refreshed

            assert await asyncio.wait_for(refresh_while_documentation_transaction_is_open(), timeout=2)
            await documentation_db.commit()
    finally:
        reset_current_execution(context_token)


@pytest.mark.asyncio
async def test_binary_artifacts_are_loaded_separately_and_deleted_on_finish(
    postgres_session_factory: SessionFactory,
) -> None:
    session_id = await _create_session(postgres_session_factory)
    async with postgres_session_factory() as db:
        repo = JobRepository(db)
        artifact_job_id = await repo.create_job(
            {"value": "artifact"},
            "test.artifact",
            session_id,
            execution_payload=_execution_payload("artifact"),
            binary_artifacts={"upload": b"binary-payload"},
        )
        assert await repo.get_job_artifacts(artifact_job_id) == {"upload": b"binary-payload"}
        await db.commit()

    artifact_claim = await _claim(postgres_session_factory, worker_id="worker-artifact")
    assert artifact_claim is not None
    assert "binary-payload" not in str(artifact_claim.execution_payload)
    async with postgres_session_factory() as db:
        repo = JobRepository(db)
        await repo.finish_claimed_job(
            artifact_job_id,
            {"value": "artifact"},
            worker_id=artifact_claim.worker_id,
            execution_token=artifact_claim.execution_token,
        )
        assert await repo.get_job_artifacts(artifact_job_id) == {}
        await db.commit()


@pytest.mark.asyncio
async def test_durable_runner_deserializes_publishes_and_finalizes(
    postgres_session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = await _create_session(postgres_session_factory)
    execution_payload = build_execution_payload(
        worker=durable_echo_worker,
        worker_args=("hello",),
        worker_kwargs={},
        dynamic_input_provider=None,
        session_result_key="echoOutput",
        await_documentation=False,
        await_documentation_timeout=None,
    )
    async with postgres_session_factory() as db:
        echo_job_id = await JobRepository(db).create_job(
            {"value": "hello", "skipCache": True},
            "test.echo",
            session_id,
            execution_payload=execution_payload,
        )
        await SessionRepository(db).update_session(session_id, {"echoJobId": str(echo_job_id)})
        await db.commit()

    echo_claim = await _claim(postgres_session_factory, worker_id="worker-echo")
    assert echo_claim is not None
    monkeypatch.setattr("src.jobs.runner.async_session_maker", postgres_session_factory)
    monkeypatch.setattr("src.jobs.lifecycle.async_session_maker", postgres_session_factory)
    monkeypatch.setattr("src.jobs.session_persistence.async_session_maker", postgres_session_factory)
    await execute_claimed_job(echo_claim)

    async with postgres_session_factory() as db:
        echo_job = await JobRepository(db).get_job(echo_job_id)
        echo_output = await SessionRepository(db).get_session_data(session_id, "echoOutput")
    assert echo_job is not None
    assert echo_job.status == "finished"
    assert echo_job.result == {"value": "hello", "jobId": str(echo_job_id)}
    assert echo_job.execution_payload is None
    assert echo_output == {"value": "hello", "jobId": str(echo_job_id)}


@pytest.mark.asyncio
async def test_invalid_queued_job_is_failed_explicitly(
    postgres_session_factory: SessionFactory,
) -> None:
    session_id = await _create_session(postgres_session_factory)
    invalid_job_id = uuid4()
    async with postgres_session_factory() as db:
        db.add(
            Job(
                job_id=invalid_job_id,
                session_id=session_id,
                job_type="test.invalid",
                status="queued",
                input={},
                normalized_input_hash=normalized_input_fingerprint({}),
                execution_payload=None,
            )
        )
        await db.commit()

    async with postgres_session_factory() as db:
        assert await JobRepository(db).fail_invalid_queued_jobs() == 1
        invalid_job = await JobRepository(db).get_job(invalid_job_id)
        await db.commit()
    assert invalid_job is not None
    assert invalid_job.status == "failed"
    assert invalid_job.errors == ["Queued job has no valid durable execution payload and cannot be executed."]


@pytest.mark.asyncio
async def test_concurrent_progress_writers_do_not_collide_on_the_progress_row(
    postgres_session_factory: SessionFactory,
) -> None:
    """A stage update and a completion count can run at the same time.

    Both used to check for the progress row and insert it when absent, so two
    writers could both insert and the loser aborted its caller's transaction.
    """
    session_id = await _create_session(postgres_session_factory)
    job_id = await _create_queued_job(postgres_session_factory, session_id)

    async with postgres_session_factory() as db:
        await db.execute(delete(JobProgress).where(JobProgress.job_id == job_id))
        await db.commit()

    async def report_stage() -> None:
        async with postgres_session_factory() as db:
            await JobRepository(db).update_job_progress(job_id, stage="chunking", message="working")
            await db.commit()

    async def report_completion() -> None:
        async with postgres_session_factory() as db:
            await JobRepository(db).increment_processed_documents(job_id)
            await db.commit()

    await asyncio.gather(report_stage(), report_completion(), report_completion())

    async with postgres_session_factory() as db:
        rows = (await db.execute(select(JobProgress).where(JobProgress.job_id == job_id))).scalars().all()

    assert len(rows) == 1
    assert rows[0].stage == "chunking"
    assert rows[0].message == "working"
    assert rows[0].processing_completed == 2


async def _progress_stage(session_factory: SessionFactory, job_id: UUID) -> tuple[str | None, str | None]:
    async with session_factory() as db:
        progress = (await db.execute(select(JobProgress).where(JobProgress.job_id == job_id))).scalar_one_or_none()
        return (progress.stage, progress.message) if progress else (None, None)


@pytest.mark.asyncio
async def test_a_failed_job_does_not_keep_reporting_its_last_progress_stage(
    postgres_session_factory: SessionFactory,
) -> None:
    """A client polling the status must not see "failed" next to a queued stage.

    Only the success path used to move the progress row, so a job that failed
    while still waiting in the queue kept reporting stage "queue" forever.
    """
    session_id = await _create_session(postgres_session_factory)
    job_id = await _create_queued_job(postgres_session_factory, session_id)

    async with postgres_session_factory() as db:
        await JobRepository(db).update_job_progress(
            job_id, stage=JobStage.queue, message="Waiting for documentation processing to complete."
        )
        await db.commit()

    assert await _progress_stage(postgres_session_factory, job_id) == (
        "queue",
        "Waiting for documentation processing to complete.",
    )

    claimed = await _claim(postgres_session_factory, worker_id="worker-failing")
    assert claimed is not None
    async with postgres_session_factory() as db:
        await JobRepository(db).fail_claimed_job(
            job_id,
            "Session has no documentation items stored.",
            worker_id=claimed.worker_id,
            execution_token=claimed.execution_token,
        )
        await db.commit()

    stage, message = await _progress_stage(postgres_session_factory, job_id)
    assert stage == "failed"
    assert message == "Session has no documentation items stored."


@pytest.mark.asyncio
async def test_a_job_failed_by_the_reaper_also_reports_the_failed_stage(
    postgres_session_factory: SessionFactory,
) -> None:
    session_id = await _create_session(postgres_session_factory)
    invalid_job_id = uuid4()
    async with postgres_session_factory() as db:
        db.add(
            Job(
                job_id=invalid_job_id,
                session_id=session_id,
                job_type="test.invalid",
                status="queued",
                input={},
                normalized_input_hash=normalized_input_fingerprint({}),
                execution_payload=None,
            )
        )
        db.add(JobProgress(job_id=invalid_job_id, stage=JobStage.queue.value))
        await db.commit()

    async with postgres_session_factory() as db:
        assert await JobRepository(db).fail_invalid_queued_jobs() == 1
        await db.commit()

    stage, message = await _progress_stage(postgres_session_factory, invalid_job_id)
    assert stage == "failed"
    assert message == "Queued job has no valid durable execution payload and cannot be executed."


@pytest.mark.asyncio
async def test_an_expected_domain_failure_is_logged_without_a_stack_trace(
    postgres_session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A session with no documentation is a user-visible outcome, not a crash.

    It used to surface as an unhandled ValueError, so the log carried a full
    traceback that buried the one line a reader needs.
    """
    session_id = await _create_session(postgres_session_factory)
    execution_payload = build_execution_payload(
        worker=durable_missing_documentation_worker,
        worker_args=(session_id,),
        worker_kwargs={},
        dynamic_input_provider=None,
        session_result_key=None,
        await_documentation=False,
        await_documentation_timeout=None,
    )
    async with postgres_session_factory() as db:
        job_id = await JobRepository(db).create_job(
            {"skipCache": True}, "test.missingDocumentation", session_id, execution_payload=execution_payload
        )
        await db.commit()

    claim = await _claim(postgres_session_factory, worker_id="worker-domain-failure")
    assert claim is not None
    monkeypatch.setattr("src.jobs.runner.async_session_maker", postgres_session_factory)
    monkeypatch.setattr("src.jobs.lifecycle.async_session_maker", postgres_session_factory)

    with caplog.at_level(logging.ERROR, logger="src.jobs.runner"):
        await execute_claimed_job(claim)

    records = [record for record in caplog.records if record.name == "src.jobs.runner"]
    assert len(records) == 1
    assert records[0].exc_info is None
    assert "has no stored documentation" in records[0].getMessage()

    async with postgres_session_factory() as db:
        job = await JobRepository(db).get_job(job_id)
    assert job is not None
    assert job.status == "failed"


@pytest.mark.asyncio
async def test_a_server_app_error_is_logged_with_its_exception_chain(
    postgres_session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    session_id = await _create_session(postgres_session_factory)
    execution_payload = build_execution_payload(
        worker=durable_unavailable_llm_worker,
        worker_args=(),
        worker_kwargs={},
        dynamic_input_provider=None,
        session_result_key=None,
        await_documentation=False,
        await_documentation_timeout=None,
    )
    async with postgres_session_factory() as db:
        job_id = await JobRepository(db).create_job(
            {"skipCache": True}, "test.unavailableLlm", session_id, execution_payload=execution_payload
        )
        await db.commit()

    claim = await _claim(postgres_session_factory, worker_id="worker-infrastructure-failure")
    assert claim is not None
    monkeypatch.setattr("src.jobs.runner.async_session_maker", postgres_session_factory)
    monkeypatch.setattr("src.jobs.lifecycle.async_session_maker", postgres_session_factory)

    with caplog.at_level(logging.ERROR, logger="src.jobs.runner"):
        await execute_claimed_job(claim)

    records = [record for record in caplog.records if record.name == "src.jobs.runner"]
    assert len(records) == 1
    assert records[0].exc_info is not None
    assert isinstance(records[0].exc_info[1], LLMUnavailableError)
    assert "TimeoutError: provider timed out" in caplog.text

    async with postgres_session_factory() as db:
        job = await JobRepository(db).get_job(job_id)
    assert job is not None
    assert job.status == "failed"
