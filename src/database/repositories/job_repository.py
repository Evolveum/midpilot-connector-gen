# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Union
from uuid import UUID, uuid4

from sqlalchemy import ColumnElement, and_, case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, defer, load_only

from src.core.errors import ExecutionOwnershipLostError
from src.database.models import Job, JobArtifact, JobProgress, Session
from src.shared.clock import utc_now
from src.shared.enums import JobStage, JobStatus
from src.shared.json_values import to_jsonable
from src.shared.normalize import normalized_input_fingerprint

logger = logging.getLogger(__name__)

_JOB_LISTING_COLUMNS = (
    Job.job_id,
    Job.job_type,
    Job.status,
    Job.created_at,
    Job.updated_at,
    Job.started_at,
    Job.finished_at,
    Job.attempt_count,
    Job.max_attempts,
    Job.worker_id,
    Job.claim_expires_at,
)

_JOB_STATUS_COLUMNS = (
    Job.job_id,
    Job.job_type,
    Job.status,
    Job.created_at,
    Job.updated_at,
    Job.started_at,
    Job.result,
    Job.errors,
)


@dataclass(frozen=True)
class ClaimedJob:
    """Immutable snapshot returned by an atomic queue claim."""

    job_id: UUID
    session_id: UUID
    job_type: str
    input_payload: Dict[str, Any]
    execution_payload: Dict[str, Any]
    worker_id: str
    execution_token: UUID
    attempt_count: int


class JobRepository:
    """Repository for job data access operations."""

    def __init__(self, db: AsyncSession):
        """
        Initialize repository with database session.

        :param db: SQLAlchemy AsyncSession
        """
        self.db = db

    async def create_job(
        self,
        input_payload: Dict[str, Any],
        job_type: str,
        session_id: UUID,
        *,
        execution_payload: Dict[str, Any],
        binary_artifacts: Mapping[str, bytes] | None = None,
        documentation_wait_timeout_seconds: float | None = None,
        max_attempts: int = 3,
    ) -> UUID:
        """
        Create a queued job and return job_id.

        :param input_payload: Job input data
        :param job_type: Type of job
        :param session_id: Associated session ID
        :param documentation_wait_timeout_seconds: Pre-claim queue wait budget,
            measured from durable job creation. ``None`` means the job does not
            wait for the session's documentation jobs at all.
        :return: Job ID
        """

        json_input = to_jsonable(input_payload)

        now = utc_now()
        documentation_wait_until = (
            now + timedelta(seconds=documentation_wait_timeout_seconds)
            if documentation_wait_timeout_seconds is not None
            else None
        )
        job = Job(
            session_id=session_id,
            job_type=job_type,
            status=JobStatus.queued.value,
            input=json_input,
            normalized_input_hash=normalized_input_fingerprint(json_input),
            execution_payload=execution_payload,
            documentation_wait_until=documentation_wait_until,
            max_attempts=max_attempts,
        )
        self.db.add(job)
        await self.db.flush()

        progress = JobProgress(
            job_id=job.job_id,
        )

        self.db.add(progress)
        for name, data in (binary_artifacts or {}).items():
            self.db.add(JobArtifact(job_id=job.job_id, name=name, data=data))
        await self.db.flush()

        logger.info("Created job %s of type %s for session %s", job.job_id, job_type, session_id)
        return job.job_id

    async def get_job(self, job_id: UUID) -> Optional[Job]:
        """
        Get a job by ID.

        :param job_id: Job ID
        :return: Job model or None
        """
        query = select(Job).where(Job.job_id == job_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_job_for_session(self, job_id: UUID, session_id: UUID) -> Optional[Job]:
        """Get a job only when it belongs to the specified session."""
        query = select(Job).where(Job.job_id == job_id, Job.session_id == session_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_job_by_input(
        self,
        job_type: str,
        input_payload: Dict[str, Any],
        date_since: datetime,
        *,
        requesting_session_id: UUID,
    ) -> Optional[Job]:
        """Get a reusable job by input within the requesting session's tenant.

        :param job_type: Type of job to look for
        :param input_payload: Input payload dict to match
        :param date_since: Earliest acceptable job creation time
        :param requesting_session_id: Session requesting reuse. Sessions reuse
            jobs from other sessions with the same API-key owner. Ownerless
            sessions (``api_key_id`` NULL) form a single tenant and reuse each
            other's jobs; when ``AUTH__API_KEY_REQUIRED`` is off every session
            is ownerless, so this yields deployment-wide reuse.
        :return: Job model or None
        """
        candidate_session = aliased(Session)
        requesting_session = aliased(Session)
        requesting_owner_id = (
            select(requesting_session.api_key_id)
            .where(requesting_session.session_id == requesting_session_id)
            .scalar_subquery()
        )
        tenant_scope = or_(
            Job.session_id == requesting_session_id,
            candidate_session.api_key_id.is_not_distinct_from(requesting_owner_id),
        )
        query = (
            select(Job)
            .join(candidate_session, candidate_session.session_id == Job.session_id)
            .where(
                Job.job_type == job_type,
                Job.normalized_input_hash == normalized_input_fingerprint(to_jsonable(input_payload)),
                Job.created_at >= date_since,
                Job.status == "finished",
                tenant_scope,
            )
            .order_by(Job.created_at.desc())
            .limit(1)
            .options(defer(Job.input, raiseload=True))
        )
        result = await self.db.execute(query)

        return result.scalars().first()

    @staticmethod
    def _execution_lock_key(job_id: UUID) -> int:
        """Map a UUID to a stable signed key for PostgreSQL advisory locks."""
        return int.from_bytes(job_id.bytes[:8], byteorder="big", signed=True)

    @staticmethod
    def _owned_claim_filter(
        job_id: UUID,
        *,
        worker_id: str,
        execution_token: UUID,
        now: datetime,
    ) -> tuple[ColumnElement[bool], ...]:
        """Match a job row only while this execution still owns its live claim.

        This is the single definition of "the claim is mine and has not expired";
        every ownership-fenced read, write and finalization builds its predicate
        from here so the conditions cannot drift apart.
        """
        return (
            Job.job_id == job_id,
            Job.status == JobStatus.running.value,
            Job.worker_id == worker_id,
            Job.execution_token == execution_token,
            Job.claim_expires_at > now,
        )

    async def _fence_claimed_write(
        self,
        job_id: UUID,
        *,
        worker_id: Optional[str],
        execution_token: Optional[UUID],
        now: datetime,
    ) -> bool:
        """Fence a side-effect write behind the caller's execution claim.

        Returns ``True`` when the write was fenced, meaning the caller passed a
        claim and still owns it, so ``jobs.updated_at`` has already been bumped.
        Returns ``False`` when the caller holds no claim at all and therefore owns
        refreshing ``updated_at`` itself. Raises ``ExecutionOwnershipLostError``
        when a claim was passed but has since been taken over or expired.
        """
        if worker_id is None or execution_token is None:
            return False
        statement = (
            update(Job)
            .where(
                *self._owned_claim_filter(
                    job_id,
                    worker_id=worker_id,
                    execution_token=execution_token,
                    now=now,
                )
            )
            .values(updated_at=now)
        )
        if not bool(getattr(await self.db.execute(statement), "rowcount", 0)):
            raise ExecutionOwnershipLostError(job_id)
        return True

    async def acquire_execution_fence(
        self,
        job_id: UUID,
        *,
        worker_id: str,
        execution_token: UUID,
    ) -> None:
        """Hold a shared transaction fence and verify current claim ownership.

        Claim takeover acquires the matching exclusive advisory lock. Side
        effects can therefore finish before a takeover, but a superseded
        execution can never commit a new fenced side effect afterwards.
        """
        await self.db.execute(select(func.pg_advisory_xact_lock_shared(self._execution_lock_key(job_id))))
        if not await self.is_execution_current(
            job_id,
            worker_id=worker_id,
            execution_token=execution_token,
        ):
            raise ExecutionOwnershipLostError(job_id)

    async def append_job_error(
        self,
        job_id: UUID,
        message: str,
        *,
        worker_id: Optional[str] = None,
        execution_token: Optional[UUID] = None,
    ) -> None:
        """
        Append a non-fatal error message to the job record without changing its status.

        :param job_id: Job ID
        :param message: Error message to append
        """
        now = utc_now()
        await self._fence_claimed_write(job_id, worker_id=worker_id, execution_token=execution_token, now=now)
        job = await self.get_job(job_id)
        if job is None:
            return

        errors_list = list(job.errors or [])
        if message not in errors_list:
            errors_list.append(message)
        job.errors = errors_list
        job.updated_at = now

        await self.db.flush()
        logger.warning("Appended error to job %s: %s", job_id, message)

    async def update_job_progress(
        self,
        job_id: UUID,
        *,
        stage: Optional[Union[str, JobStage]] = None,
        message: Optional[str] = None,
        total_processing: Optional[int] = None,
        processing_completed: Optional[int] = None,
        worker_id: Optional[str] = None,
        execution_token: Optional[UUID] = None,
    ) -> None:
        """
        Update progress information for a running job.

        :param job_id: Job ID
        :param stage: Progress stage
        :param message: Progress message
        :param total_processing: Total number of documents
        :param processing_completed: Number of processed documents
        """
        now = utc_now()
        fenced = await self._fence_claimed_write(
            job_id,
            worker_id=worker_id,
            execution_token=execution_token,
            now=now,
        )

        changed: Dict[str, Any] = {"updated_at": now}
        if stage is not None:
            changed["stage"] = stage.value if isinstance(stage, JobStage) else stage
        if message is not None:
            changed["message"] = message
        if total_processing is not None:
            changed["total_processing"] = total_processing
        if processing_completed is not None:
            changed["processing_completed"] = processing_completed

        await self._upsert_progress(job_id, changed)

        if not fenced:
            await self.db.execute(update(Job).where(Job.job_id == job_id).values(updated_at=now))

        await self.db.flush()

    async def _upsert_progress(self, job_id: UUID, changed: Dict[str, Any]) -> None:
        """Write the job's progress row, creating it when it does not exist yet.

        Upsert rather than select-then-insert: a stage update and a chunk
        completion for the same job can run concurrently, and both would
        otherwise observe no row and insert one.
        """
        await self.db.execute(
            pg_insert(JobProgress)
            .values(job_id=job_id, **changed)
            .on_conflict_do_update(index_elements=[JobProgress.job_id], set_=changed)
        )

    async def _mark_progress_failed(self, job_id: UUID, *, now: datetime, message: str) -> None:
        """Move the progress row to the failed stage alongside the job row.

        Without this a failed job keeps whatever stage it last reported, so a
        client polling the status sees "failed" next to a progress block still
        claiming the job is queued or running. The caller has already refreshed
        the job's own ``updated_at``, so only the progress row is written here.
        """
        await self._upsert_progress(
            job_id,
            {"stage": JobStage.failed.value, "message": message, "updated_at": now},
        )

    async def update_job_input(
        self,
        job_id: UUID,
        new_input: Dict[str, Any],
        *,
        worker_id: Optional[str] = None,
        execution_token: Optional[UUID] = None,
    ) -> None:
        """
        Update the input payload of a job.

        :param job_id: Job ID
        :param new_input: New input payload
        """
        if worker_id is not None and execution_token is not None:
            job = (
                await self.db.execute(
                    select(Job)
                    .where(
                        *self._owned_claim_filter(
                            job_id,
                            worker_id=worker_id,
                            execution_token=execution_token,
                            now=utc_now(),
                        )
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
        else:
            job = await self.get_job(job_id)
        if job is None:
            if worker_id is not None and execution_token is not None:
                raise ExecutionOwnershipLostError(job_id)
            raise FileNotFoundError(f"Job {job_id} not found")

        json_input = to_jsonable(new_input)

        job.input = json_input
        job.normalized_input_hash = normalized_input_fingerprint(json_input)
        job.updated_at = utc_now()

        await self.db.flush()
        logger.info("Updated input for job %s", job_id)

    async def increment_processed_documents(
        self,
        job_id: UUID,
        delta: int = 1,
        *,
        worker_id: Optional[str] = None,
        execution_token: Optional[UUID] = None,
    ) -> None:
        """
        Increment the number of fully processed documents.

        :param job_id: Job ID
        :param delta: Number to increment by
        """

        now = utc_now()
        if not await self._fence_claimed_write(
            job_id,
            worker_id=worker_id,
            execution_token=execution_token,
            now=now,
        ):
            await self.db.execute(update(Job).where(Job.job_id == job_id).values(updated_at=now))

        await self.db.execute(
            pg_insert(JobProgress)
            .values(job_id=job_id, processing_completed=delta, updated_at=now)
            .on_conflict_do_update(
                index_elements=[JobProgress.job_id],
                set_={
                    "processing_completed": func.coalesce(JobProgress.processing_completed, 0) + delta,
                    "updated_at": now,
                },
            )
        )

        await self.db.flush()

    async def get_job_status(self, job_id: UUID) -> Dict[str, Any]:
        """
        Return a public job status dict.

        A missing job is a normal result and reports ``not_found``. Database and
        mapping failures propagate: reporting them as a missing job tells the
        client the job never existed and hides the outage from the caller.

        :param job_id: Job ID
        :return: Job status dict
        """
        job = (
            await self.db.execute(
                select(Job).where(Job.job_id == job_id).options(load_only(*_JOB_STATUS_COLUMNS, raiseload=True))
            )
        ).scalar_one_or_none()
        if job is None:
            return {"jobId": str(job_id), "status": "not_found"}

        query = select(JobProgress).where(JobProgress.job_id == job_id)
        result = await self.db.execute(query)
        progress = result.scalar_one_or_none()

        out: Dict[str, Any] = {
            "jobId": str(job.job_id),
            "status": job.status,
            "createdAt": job.created_at.isoformat(),
            "updatedAt": job.updated_at.isoformat(),
        }

        if job.started_at:
            out["startedAt"] = job.started_at.isoformat()

        if progress:
            progress_dict: Dict[str, Union[str, int]] = {}
            if progress.stage:
                progress_dict["stage"] = progress.stage
            if progress.message:
                progress_dict["message"] = progress.message

            if job.job_type == "scrape.getRelevantDocumentation":
                if progress.total_processing is not None:
                    progress_dict["totalIterations"] = progress.total_processing
                if progress.processing_completed is not None:
                    progress_dict["completedIterations"] = progress.processing_completed
            else:
                if progress.total_processing is not None:
                    progress_dict["totalDocuments"] = progress.total_processing
                if progress.processing_completed is not None:
                    progress_dict["processedDocuments"] = progress.processing_completed

            if progress_dict:
                out["progress"] = progress_dict

        if job.status == JobStatus.finished.value and job.result:
            out["result"] = job.result

        if job.errors:
            out["errors"] = job.errors

        return out

    async def claim_next_job(
        self,
        *,
        worker_id: str,
        claim_timeout_seconds: float,
        job_type: Optional[str] = None,
    ) -> Optional[ClaimedJob]:
        """Atomically claim one queued or abandoned job.

        PostgreSQL row locking with ``SKIP LOCKED`` guarantees that concurrent
        worker processes cannot receive the same execution token.
        """
        now = utc_now()
        claimable_state = or_(
            Job.status == JobStatus.queued.value,
            and_(
                Job.status == JobStatus.running.value,
                Job.claim_expires_at.is_not(None),
                Job.claim_expires_at <= now,
            ),
        )
        documentation_job = aliased(Job)
        pending_documentation_exists = (
            select(documentation_job.job_id)
            .where(
                documentation_job.session_id == Job.session_id,
                documentation_job.job_id != Job.job_id,
                documentation_job.job_type.in_(("scrape.getRelevantDocumentation", "documentation.processUpload")),
                documentation_job.status.not_in((JobStatus.finished.value, JobStatus.failed.value)),
            )
            .exists()
        )
        documentation_ready = or_(
            Job.documentation_wait_until.is_(None),
            Job.documentation_wait_until <= now,
            ~pending_documentation_exists,
        )
        query = (
            select(Job)
            .where(
                claimable_state,
                func.jsonb_typeof(Job.execution_payload) == "object",
                Job.available_at <= now,
                Job.attempt_count < Job.max_attempts,
                documentation_ready,
            )
            .order_by(Job.available_at, Job.created_at, Job.job_id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job_type:
            query = query.where(Job.job_type == job_type)

        job = (await self.db.execute(query)).scalar_one_or_none()
        if job is None or job.execution_payload is None:
            return None

        if job.documentation_wait_until is not None and job.documentation_wait_until <= now:
            pending_documentation_job_id = (
                await self.db.execute(
                    select(documentation_job.job_id)
                    .where(
                        documentation_job.session_id == job.session_id,
                        documentation_job.job_id != job.job_id,
                        documentation_job.job_type.in_(
                            ("scrape.getRelevantDocumentation", "documentation.processUpload")
                        ),
                        documentation_job.status.not_in((JobStatus.finished.value, JobStatus.failed.value)),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if pending_documentation_job_id is not None:
                timeout_message = (
                    "Timed out waiting for documentation processing; continuing while "
                    f"documentation job {pending_documentation_job_id} is still unfinished."
                )
                logger.warning("Job %s: %s", job.job_id, timeout_message)
                errors = list(job.errors or [])
                if timeout_message not in errors:
                    errors.append(timeout_message)
                    job.errors = errors

        await self.db.execute(select(func.pg_advisory_xact_lock(self._execution_lock_key(job.job_id))))
        execution_token = uuid4()
        job.status = JobStatus.running.value
        job.worker_id = worker_id
        job.execution_token = execution_token
        job.claim_expires_at = now + timedelta(seconds=claim_timeout_seconds)
        job.attempt_count += 1
        job.updated_at = now
        if job.started_at is None:
            job.started_at = now
        await self.db.flush()

        logger.info(
            "Worker %s claimed job %s (attempt %s/%s)",
            worker_id,
            job.job_id,
            job.attempt_count,
            job.max_attempts,
        )
        return ClaimedJob(
            job_id=job.job_id,
            session_id=job.session_id,
            job_type=job.job_type,
            input_payload=dict(job.input),
            execution_payload=dict(job.execution_payload),
            worker_id=worker_id,
            execution_token=execution_token,
            attempt_count=job.attempt_count,
        )

    async def get_job_artifacts(self, job_id: UUID) -> Dict[str, bytes]:
        """Load named binary execution inputs without joining them into claims."""
        rows = (
            await self.db.execute(select(JobArtifact.name, JobArtifact.data).where(JobArtifact.job_id == job_id))
        ).all()
        return {name: bytes(data) for name, data in rows}

    async def _delete_execution_artifacts(self, job_id: UUID) -> None:
        await self.db.execute(delete(JobArtifact).where(JobArtifact.job_id == job_id))

    async def refresh_claim(
        self,
        job_id: UUID,
        *,
        worker_id: str,
        execution_token: UUID,
        claim_timeout_seconds: float,
    ) -> bool:
        """Refresh a live claim if and only if this execution still owns it."""
        now = utc_now()
        statement = (
            update(Job)
            .where(
                *self._owned_claim_filter(
                    job_id,
                    worker_id=worker_id,
                    execution_token=execution_token,
                    now=now,
                )
            )
            .values(
                claim_expires_at=now + timedelta(seconds=claim_timeout_seconds),
                updated_at=now,
            )
        )
        result = await self.db.execute(statement)
        return bool(getattr(result, "rowcount", 0))

    async def is_execution_current(
        self,
        job_id: UUID,
        *,
        worker_id: str,
        execution_token: UUID,
    ) -> bool:
        """Check execution ownership, including claim validity."""
        query = select(Job.job_id).where(
            *self._owned_claim_filter(
                job_id,
                worker_id=worker_id,
                execution_token=execution_token,
                now=utc_now(),
            )
        )
        return (await self.db.execute(query)).scalar_one_or_none() is not None

    async def finish_claimed_job(
        self,
        job_id: UUID,
        result: Dict[str, Any],
        *,
        worker_id: str,
        execution_token: UUID,
    ) -> Optional[Dict[str, Any]]:
        """Finalize only the execution that currently owns the job claim."""
        query = (
            select(Job)
            .where(
                *self._owned_claim_filter(
                    job_id,
                    worker_id=worker_id,
                    execution_token=execution_token,
                    now=utc_now(),
                )
            )
            .with_for_update()
        )
        job = (await self.db.execute(query)).scalar_one_or_none()
        if job is None:
            return None

        now = utc_now()
        job.status = JobStatus.finished.value
        job.updated_at = now
        job.finished_at = now
        job.result = to_jsonable(result)
        job.execution_payload = None
        await self._delete_execution_artifacts(job_id)
        job.worker_id = None
        job.execution_token = None
        job.claim_expires_at = None
        await self.update_job_progress(job_id, stage=JobStage.finished, message="completed")
        await self.db.flush()
        logger.info("Job %s set to finished by worker %s", job_id, worker_id)
        return {
            "id": job.job_id,
            "status": job.status,
            "result": job.result,
            "createdAt": job.created_at.isoformat(),
            "updatedAt": job.updated_at.isoformat(),
            "finishedAt": job.finished_at.isoformat(),
        }

    async def fail_claimed_job(
        self,
        job_id: UUID,
        error: str,
        *,
        worker_id: str,
        execution_token: UUID,
    ) -> Optional[Dict[str, Any]]:
        """Fail only the execution that currently owns the job claim."""
        query = (
            select(Job)
            .where(
                *self._owned_claim_filter(
                    job_id,
                    worker_id=worker_id,
                    execution_token=execution_token,
                    now=utc_now(),
                )
            )
            .with_for_update()
        )
        job = (await self.db.execute(query)).scalar_one_or_none()
        if job is None:
            return None

        now = utc_now()
        lines = [line for line in str(error).splitlines() if line.strip()]
        errors = list(job.errors or [])
        errors.extend(line for line in lines if line not in errors)
        job.errors = errors
        job.status = JobStatus.failed.value
        job.updated_at = now
        job.finished_at = now
        job.execution_payload = None
        await self._delete_execution_artifacts(job_id)
        job.worker_id = None
        job.execution_token = None
        job.claim_expires_at = None
        await self._mark_progress_failed(job_id, now=now, message=lines[0] if lines else "failed")
        await self.db.flush()
        logger.error("Job %s set to failed by worker %s: %s", job_id, worker_id, error)
        return {
            "id": str(job.job_id),
            "type": job.job_type,
            "status": job.status,
            "errors": job.errors,
            "createdAt": job.created_at.isoformat(),
            "updatedAt": job.updated_at.isoformat(),
        }

    async def release_claim(
        self,
        job_id: UUID,
        *,
        worker_id: str,
        execution_token: UUID,
    ) -> bool:
        """Return a gracefully interrupted execution to the queue.

        Deliberately does not use ``_owned_claim_filter``: a shutting-down worker
        must still hand its job back after the claim deadline has passed, so this
        is the one ownership check that intentionally omits ``claim_expires_at``.
        """
        now = utc_now()
        statement = (
            update(Job)
            .where(
                Job.job_id == job_id,
                Job.status == JobStatus.running.value,
                Job.worker_id == worker_id,
                Job.execution_token == execution_token,
            )
            .values(
                status=JobStatus.queued.value,
                worker_id=None,
                execution_token=None,
                claim_expires_at=None,
                available_at=now,
                updated_at=now,
                attempt_count=case((Job.attempt_count > 0, Job.attempt_count - 1), else_=0),
            )
        )
        result = await self.db.execute(statement)
        released = bool(getattr(result, "rowcount", 0))
        if released:
            logger.info("Worker %s returned interrupted job %s to the queue", worker_id, job_id)
        return released

    async def fail_expired_exhausted_jobs(self) -> int:
        """Fail abandoned jobs whose crash-retry budget is exhausted."""
        now = utc_now()
        query = (
            select(Job)
            .where(
                Job.status == JobStatus.running.value,
                Job.claim_expires_at.is_not(None),
                Job.claim_expires_at <= now,
                Job.attempt_count >= Job.max_attempts,
            )
            .with_for_update(skip_locked=True)
            .limit(100)
        )
        jobs = (await self.db.execute(query)).scalars().all()
        for job in jobs:
            await self.db.execute(select(func.pg_advisory_xact_lock(self._execution_lock_key(job.job_id))))
            job.status = JobStatus.failed.value
            job.updated_at = now
            job.finished_at = now
            job.execution_payload = None
            await self._delete_execution_artifacts(job.job_id)
            job.worker_id = None
            job.execution_token = None
            job.claim_expires_at = None
            message = f"Job claim expired after {job.attempt_count} execution attempts."
            errors = list(job.errors or [])
            if message not in errors:
                errors.append(message)
            job.errors = errors
            await self._mark_progress_failed(job.job_id, now=now, message=message)
        if jobs:
            await self.db.flush()
            logger.error("Failed %s jobs whose claim retry budget was exhausted", len(jobs))
        return len(jobs)

    async def fail_invalid_queued_jobs(self) -> int:
        """Fail queued rows that cannot be deserialized by any worker."""
        now = utc_now()
        jobs = (
            (
                await self.db.execute(
                    select(Job)
                    .where(
                        Job.status == JobStatus.queued.value,
                        or_(
                            Job.execution_payload.is_(None),
                            func.jsonb_typeof(Job.execution_payload) != "object",
                        ),
                    )
                    .with_for_update(skip_locked=True)
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
        for job in jobs:
            job.status = JobStatus.failed.value
            job.updated_at = now
            job.finished_at = now
            await self._delete_execution_artifacts(job.job_id)
            message = "Queued job has no valid durable execution payload and cannot be executed."
            errors = list(job.errors or [])
            if message not in errors:
                errors.append(message)
            job.errors = errors
            await self._mark_progress_failed(job.job_id, now=now, message=message)
        if jobs:
            await self.db.flush()
            logger.error("Failed %s queued jobs with invalid execution payloads", len(jobs))
        return len(jobs)

    async def reap_unclaimable_jobs(self) -> int:
        """Run all low-frequency queue repair checks."""
        expired = await self.fail_expired_exhausted_jobs()
        invalid = await self.fail_invalid_queued_jobs()
        return expired + invalid

    async def get_jobs_by_session(self, session_id: UUID) -> list[Dict[str, Any]]:
        """
        Get all jobs for a given session.

        :param session_id: Session ID
        :return: List of job dicts
        """
        # A job listing never shows payloads, and input/normalized_input/
        # execution_payload/result are the largest columns in the table; loading
        # them for every job of a session would move megabytes to render a list.
        query = (
            select(Job)
            .where(Job.session_id == session_id)
            .order_by(Job.created_at)
            .options(load_only(*_JOB_LISTING_COLUMNS, raiseload=True))
        )
        result = await self.db.execute(query)
        jobs = result.scalars().all()

        job_list = []
        for job in jobs:
            job_dict = {
                "jobId": str(job.job_id),
                "type": job.job_type,
                "status": job.status,
                "createdAt": job.created_at.isoformat(),
                "attemptCount": job.attempt_count,
                "maxAttempts": job.max_attempts,
            }
            if job.updated_at:
                job_dict["updatedAt"] = job.updated_at.isoformat()
            if job.started_at:
                job_dict["startedAt"] = job.started_at.isoformat()
            if job.finished_at:
                job_dict["finishedAt"] = job.finished_at.isoformat()
            if job.status == JobStatus.running.value:
                if job.worker_id:
                    job_dict["workerId"] = job.worker_id
                if job.claim_expires_at:
                    job_dict["claimExpiresAt"] = job.claim_expires_at.isoformat()
            job_list.append(job_dict)

        return job_list
