# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.core.job_execution import (
    JobExecutionContext,
    reset_current_execution,
    set_current_execution,
)
from src.database.models import Base, DocumentationItem, Job, Session
from src.database.repositories.documentation_repository import DocumentationRepository
from src.database.repositories.job_repository import ClaimedJob, JobRepository
from src.database.repositories.session_repository import SessionRepository
from src.jobs.payload import build_execution_payload
from src.jobs.runner import execute_claimed_job


async def durable_echo_worker(value: str, *, job_id: UUID) -> dict[str, str]:
    return {"value": value, "jobId": str(job_id)}


async def _claim(
    session_factory,
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
async def test_concurrent_workers_claim_once_and_stale_execution_cannot_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        session_id = uuid4()
        job_id = uuid4()
        async with session_factory() as db:
            db.add(Session(session_id=session_id))
            db.add(
                Job(
                    job_id=job_id,
                    session_id=session_id,
                    job_type="test.worker",
                    status="queued",
                    input={"value": "test"},
                    normalized_input={"value": "test"},
                    execution_payload={
                        "version": 1,
                        "worker": "test.integration.database.test_job_worker_postgres:_claim",
                        "args": [],
                        "kwargs": {},
                    },
                    max_attempts=3,
                )
            )
            await db.commit()

        first, second = await asyncio.gather(
            _claim(session_factory, worker_id="worker-a"),
            _claim(session_factory, worker_id="worker-b"),
        )
        claims = [claim for claim in (first, second) if claim is not None]
        assert len(claims) == 1
        first_claim = claims[0]

        # Starting another process must not invalidate an active claim.
        async with session_factory() as db:
            recovered = await JobRepository(db).fail_expired_exhausted_jobs()
            active_job = await JobRepository(db).get_job(job_id)
            await db.commit()
        assert recovered == 0
        assert active_job is not None
        assert active_job.status == "running"
        assert active_job.execution_token == first_claim.execution_token

        # Simulate a hard crash by expiring the first worker's claim, then let
        # another process reclaim the same durable job.
        async with session_factory() as db:
            await db.execute(
                update(Job)
                .where(Job.job_id == job_id)
                .values(claim_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
            )
            await db.commit()

        second_claim = await _claim(session_factory, worker_id="worker-c")
        assert second_claim is not None
        assert second_claim.execution_token != first_claim.execution_token
        assert second_claim.attempt_count == 2

        async with session_factory() as db:
            stale_result = await JobRepository(db).finish_claimed_job(
                job_id,
                {"winner": "stale"},
                worker_id=first_claim.worker_id,
                execution_token=first_claim.execution_token,
            )
            await db.commit()
        assert stale_result is None

        async with session_factory() as db:
            winning_result = await JobRepository(db).finish_claimed_job(
                job_id,
                {"winner": "worker-c"},
                worker_id=second_claim.worker_id,
                execution_token=second_claim.execution_token,
            )
            await db.commit()
        assert winning_result is not None

        async with session_factory() as db:
            job = await JobRepository(db).get_job(job_id)
            assert job is not None
            assert job.status == "finished"
            assert job.result == {"winner": "worker-c"}
            assert job.worker_id is None
            assert job.execution_token is None

        # A completed older job must not overwrite a result after the session
        # pointer has moved to a newer job.
        async with session_factory() as db:
            session_repo = SessionRepository(db)
            await session_repo.update_session(session_id, {"testJobId": str(uuid4())})
            await db.commit()
        async with session_factory() as db:
            persisted = await SessionRepository(db).update_result_if_current_job(
                session_id=session_id,
                result_key="testOutput",
                job_id=job_id,
                value={"winner": "stale"},
            )
            await db.commit()
        assert persisted is False

        # Retrying the same job side effect upserts one deterministic
        # documentation row rather than duplicating chunks.
        async with session_factory() as db:
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
                    select(func.count()).select_from(DocumentationItem).where(DocumentationItem.origin_job_id == job_id)
                )
            ).scalar_one()
            await db.commit()
        assert first_chunk_id == second_chunk_id
        assert count == 1

        # Retrying origin A must not remove a link that another job B added to
        # the shared documentation row.
        linked_job_id = uuid4()
        async with session_factory() as db:
            doc_repo = DocumentationRepository(db)
            assert await doc_repo.update_documentation_item(
                first_chunk_id,
                original_job_id=linked_job_id,
            )
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
                await db.execute(select(DocumentationItem).where(DocumentationItem.chunk_id == first_chunk_id))
            ).scalar_one()
            await db.commit()
        assert set(linked_item.scrape_job_ids) == {str(job_id), str(linked_job_id)}

        # A documentation-dependent job remains queued and consumes no
        # execution attempt while a producer job in the session is unfinished.
        dependency_payload = build_execution_payload(
            worker=durable_echo_worker,
            worker_args=("dependent",),
            worker_kwargs={},
            dynamic_input_provider=None,
            session_result_key=None,
            await_documentation=True,
            await_documentation_timeout=750,
        )
        producer_payload = build_execution_payload(
            worker=durable_echo_worker,
            worker_args=("producer",),
            worker_kwargs={},
            dynamic_input_provider=None,
            session_result_key=None,
            await_documentation=False,
            await_documentation_timeout=None,
        )
        async with session_factory() as db:
            repo = JobRepository(db)
            dependent_job_id = await repo.create_job(
                {"value": "dependent"},
                "digester.getAuth",
                session_id,
                execution_payload=dependency_payload,
                waits_for_documentation=True,
                documentation_wait_timeout_seconds=750,
            )
            producer_job_id = await repo.create_job(
                {"value": "producer"},
                "documentation.processUpload",
                session_id,
                execution_payload=producer_payload,
            )
            await db.commit()

        producer_claim = await _claim(session_factory, worker_id="worker-producer")
        assert producer_claim is not None
        assert producer_claim.job_id == producer_job_id
        async with session_factory() as db:
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

        dependent_claim = await _claim(session_factory, worker_id="worker-dependent")
        assert dependent_claim is not None
        assert dependent_claim.job_id == dependent_job_id

        # A fenced documentation transaction no longer locks the jobs row, so
        # claim refresh on another connection remains live.
        execution_context_token = set_current_execution(
            JobExecutionContext(
                job_id=dependent_claim.job_id,
                worker_id=dependent_claim.worker_id,
                execution_token=dependent_claim.execution_token,
            )
        )
        try:
            async with session_factory() as documentation_db:
                await DocumentationRepository(documentation_db).create_documentation_item(
                    session_id,
                    "upload",
                    "heartbeat-safe content",
                    original_job_id=dependent_job_id,
                    doc_id=uuid4(),
                    url="upload://heartbeat-safe.txt",
                    metadata={"chunk_number": 0},
                )

                async def refresh_while_documentation_transaction_is_open() -> bool:
                    async with session_factory() as heartbeat_db:
                        refreshed = await JobRepository(heartbeat_db).refresh_claim(
                            dependent_job_id,
                            worker_id=dependent_claim.worker_id,
                            execution_token=dependent_claim.execution_token,
                            claim_timeout_seconds=60,
                        )
                        await heartbeat_db.commit()
                        return refreshed

                assert await asyncio.wait_for(
                    refresh_while_documentation_transaction_is_open(),
                    timeout=2,
                )
                await documentation_db.commit()
        finally:
            reset_current_execution(execution_context_token)

        # Binary execution artifacts are loaded separately from the claim and
        # removed with terminal execution state.
        artifact_job_id = uuid4()
        async with session_factory() as db:
            repo = JobRepository(db)
            artifact_job_id = await repo.create_job(
                {"value": "artifact"},
                "test.artifact",
                session_id,
                execution_payload=producer_payload,
                binary_artifacts={"upload": b"binary-payload"},
            )
            assert await repo.get_job_artifacts(artifact_job_id) == {"upload": b"binary-payload"}
            await db.commit()

        # Finish the dependent claim so FIFO claiming reaches the artifact job.
        async with session_factory() as db:
            await JobRepository(db).finish_claimed_job(
                dependent_job_id,
                {"value": "dependent"},
                worker_id=dependent_claim.worker_id,
                execution_token=dependent_claim.execution_token,
            )
            await db.commit()
        artifact_claim = await _claim(session_factory, worker_id="worker-artifact")
        assert artifact_claim is not None
        assert artifact_claim.job_id == artifact_job_id
        assert "binary-payload" not in str(artifact_claim.execution_payload)
        async with session_factory() as db:
            repo = JobRepository(db)
            await repo.finish_claimed_job(
                artifact_job_id,
                {"value": "artifact"},
                worker_id=artifact_claim.worker_id,
                execution_token=artifact_claim.execution_token,
            )
            assert await repo.get_job_artifacts(artifact_job_id) == {}
            await db.commit()

        # Exercise the real durable runner against the isolated PostgreSQL
        # schema, including callable deserialization, fenced session write, and
        # conditional finalization.
        echo_job_id = uuid4()
        execution_payload = build_execution_payload(
            worker=durable_echo_worker,
            worker_args=("hello",),
            worker_kwargs={},
            dynamic_input_provider=None,
            session_result_key="echoOutput",
            await_documentation=False,
            await_documentation_timeout=None,
        )
        async with session_factory() as db:
            echo_job_id = await JobRepository(db).create_job(
                {"value": "hello", "skipCache": True},
                "test.echo",
                session_id,
                execution_payload=execution_payload,
            )
            await SessionRepository(db).update_session(session_id, {"echoJobId": str(echo_job_id)})
            await db.commit()

        echo_claim = await _claim(session_factory, worker_id="worker-echo")
        assert echo_claim is not None
        monkeypatch.setattr("src.jobs.runner.async_session_maker", session_factory)
        monkeypatch.setattr("src.jobs.lifecycle.async_session_maker", session_factory)
        monkeypatch.setattr("src.jobs.session_persistence.async_session_maker", session_factory)
        await execute_claimed_job(echo_claim)

        async with session_factory() as db:
            echo_job = await JobRepository(db).get_job(echo_job_id)
            echo_output = await SessionRepository(db).get_session_data(session_id, "echoOutput")
        assert echo_job is not None
        assert echo_job.status == "finished"
        assert echo_job.result == {"value": "hello", "jobId": str(echo_job_id)}
        assert echo_job.execution_payload is None
        assert echo_output == {"value": "hello", "jobId": str(echo_job_id)}

        invalid_job_id = uuid4()
        async with session_factory() as db:
            db.add(
                Job(
                    job_id=invalid_job_id,
                    session_id=session_id,
                    job_type="test.invalid",
                    status="queued",
                    input={},
                    normalized_input={},
                    execution_payload=None,
                )
            )
            await db.commit()
        async with session_factory() as db:
            assert await JobRepository(db).fail_invalid_queued_jobs() == 1
            invalid_job = await JobRepository(db).get_job(invalid_job_id)
            await db.commit()
        assert invalid_job is not None
        assert invalid_job.status == "failed"
        assert invalid_job.errors == ["Queued job has no valid durable execution payload and cannot be executed."]
    finally:
        if schema_created:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        await engine.dispose()
