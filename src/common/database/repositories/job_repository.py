#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

import logging
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional, Union
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...enums import JobStage, JobStatus
from ..models import Job, JobProgress
from .relevant_chunk_repository import RelevantChunkRepository

logger = logging.getLogger(__name__)


def to_jsonable(obj: Any) -> Any:
    """Make obj safe to store in JSON/JSONB (UUID, datetime, Enum, Pydantic, nested)."""
    if obj is None:
        return None
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if hasattr(obj, "model_dump"):  # pydantic v2
        return obj.model_dump(by_alias=True, mode="json")
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]
    return obj


def _now_iso() -> str:
    """Return current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


class JobRepository:
    """Repository for job data access operations."""

    def __init__(self, db: AsyncSession):
        """
        Initialize repository with database session.

        :param db: SQLAlchemy AsyncSession
        """
        self.db = db
        self.relevant_chunk_repo = RelevantChunkRepository(db)

    async def save_relevant_chunks(self, job_id: UUID, session_id: UUID, result: Dict[str, Any]) -> None:
        """
        Extract relevant chunks from job result and save them to the relevant_chunks table.
        Stores object class names directly as entity_type.

        :param job_id: Job ID
        :param session_id: Session ID
        :param result: Job result containing relevantChunks
        """
        try:
            chunks_to_save = []

            # Save object-class-level relevantChunks (if present in result.objectClasses)
            result_data = result.get("result", {})
            object_classes = result_data.get("objectClasses", [])

            if isinstance(object_classes, list):
                for obj_class in object_classes:
                    if not isinstance(obj_class, dict):
                        continue

                    class_name = obj_class.get("name")
                    class_chunks = obj_class.get("relevantChunks", [])

                    if class_name and class_chunks:
                        # Prepare chunks for bulk insert
                        for chunk_info in class_chunks:
                            doc_uuid_str = chunk_info.get("docUuid")
                            if doc_uuid_str:
                                chunks_to_save.append(
                                    {
                                        "entity_type": class_name,
                                        "doc_id": doc_uuid_str,
                                    }
                                )

            if chunks_to_save:
                chunks_saved = await self.relevant_chunk_repo.bulk_add_relevant_chunks(
                    session_id=session_id,
                    chunks=chunks_to_save,
                )
                if chunks_saved > 0:
                    logger.info(f"Saved {chunks_saved} relevant chunks for job {job_id}")

        except Exception as e:
            logger.warning(f"Failed to save relevant chunks for job {job_id}: {e}", exc_info=True)

    async def create_job(self, input_payload: Dict[str, Any], job_type: str, session_id: UUID) -> UUID:
        """
        Create a queued job and return job_id.

        :param input_payload: Job input data
        :param job_type: Type of job
        :param session_id: Associated session ID
        :return: Job ID
        """
        job = Job(
            session_id=session_id,
            job_type=job_type,
            status=JobStatus.queued.value,
            input=to_jsonable(input_payload),
        )
        self.db.add(job)
        await self.db.flush()

        progress = JobProgress(
            job_id=job.job_id,
            # processing_completed=0,
            # total_documents
        )

        self.db.add(progress)
        await self.db.flush()

        logger.info(f"Created job {job.job_id} of type {job_type} for session {session_id}")
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

    async def set_running(self, job_id: UUID) -> Dict[str, Any]:
        """
        Transition a queued job to running state and return the updated job record.

        :param job_id: Job ID
        :return: Job data dict
        """
        job = await self.get_job(job_id)
        if job is None:
            raise FileNotFoundError(f"Job {job_id} not found")

        job.status = JobStatus.running.value
        now = datetime.now(timezone.utc)
        if job.started_at is None:
            job.started_at = now
        job.updated_at = now

        await self.db.flush()
        logger.info(f"Job {job_id} set to running")

        return {
            "id": str(job.job_id),
            "type": job.job_type,
            "status": job.status,
            "input": job.input,
            "createdAt": job.created_at.isoformat(),
            "updatedAt": job.updated_at.isoformat(),
            "startedAt": job.started_at.isoformat() if job.started_at else None,
        }

    async def set_finished(self, job_id: UUID, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Transition a running job to finished state, attach result, and return the record.

        :param job_id: Job ID
        :param result: Job result data
        :return: Job data dict
        """
        job = await self.get_job(job_id)
        if job is None:
            raise FileNotFoundError(f"Job {job_id} not found")

        job.status = JobStatus.finished.value
        job.updated_at = datetime.now(timezone.utc)
        job.finished_at = datetime.now(timezone.utc)
        job.result = to_jsonable(result)

        # Update progress to finished stage first
        await self.update_job_progress(job_id, stage=JobStage.finished, message="completed")

        # Flush the main job changes first
        await self.db.flush()

        # Extract and save relevant chunks to the database (non-critical, so catch errors)
        try:
            await self.save_relevant_chunks(job_id, job.session_id, result)
        except Exception as e:
            logger.warning(f"Failed to save relevant chunks for job {job_id}: {e}", exc_info=True)
            # Don't fail the entire job if chunk saving fails

        logger.info(f"Job {job_id} set to finished")

        return {
            "id": job.job_id,
            "status": job.status,
            "result": job.result,
            "createdAt": job.created_at.isoformat(),
            "updatedAt": job.updated_at.isoformat(),
            "finishedAt": job.finished_at.isoformat() if job.finished_at else None,
        }

    async def set_failed(self, job_id: UUID, error: str) -> Dict[str, Any]:
        """
        Transition a job to failed state with error messages.

        :param job_id: Job ID
        :param error: Error message
        :return: Job data dict
        """
        job = await self.get_job(job_id)
        if job is None:
            raise FileNotFoundError(f"Job {job_id} not found")

        job.status = JobStatus.failed.value
        job.updated_at = datetime.now(timezone.utc)
        job.finished_at = datetime.now(timezone.utc)

        # Normalize error into list
        lines = [ln for ln in str(error).splitlines() if ln.strip()]
        existing_list = list(job.errors or [])
        for ln in lines:
            if ln not in existing_list:
                existing_list.append(ln)
        job.errors = existing_list if existing_list else lines

        await self.db.flush()
        logger.error(f"Job {job_id} set to failed: {error}")

        return {
            "id": str(job.job_id),
            "type": job.job_type,
            "status": job.status,
            "errors": job.errors,
            "createdAt": job.created_at.isoformat(),
            "updatedAt": job.updated_at.isoformat(),
        }

    async def append_job_error(self, job_id: UUID, message: str) -> None:
        """
        Append a non-fatal error message to the job record without changing its status.

        :param job_id: Job ID
        :param message: Error message to append
        """
        job = await self.get_job(job_id)
        if job is None:
            return

        errors_list = list(job.errors or [])
        if message not in errors_list:
            errors_list.append(message)
        job.errors = errors_list
        job.updated_at = datetime.now(timezone.utc)

        await self.db.flush()
        logger.warning(f"Appended error to job {job_id}: {message}")

    async def update_job_progress(
        self,
        job_id: UUID,
        *,
        stage: Optional[Union[str, JobStage]] = None,
        message: Optional[str] = None,
        total_processing: Optional[int] = None,
        processing_completed: Optional[int] = None,
    ) -> None:
        """
        Update progress information for a running job.

        :param job_id: Job ID
        :param stage: Progress stage
        :param message: Progress message
        :param total_processing: Total number of documents
        :param processing_completed: Number of processed documents
        """
        try:
            # Get or create progress record
            query = select(JobProgress).where(JobProgress.job_id == job_id)
            result = await self.db.execute(query)
            progress = result.scalar_one_or_none()

            if progress is None:
                progress = JobProgress(job_id=job_id)
                self.db.add(progress)

            # Update fields
            if stage is not None:
                progress.stage = stage.value if isinstance(stage, JobStage) else stage
            if message is not None:
                progress.message = message
            if total_processing is not None:
                progress.total_processing = total_processing
            if processing_completed is not None:
                progress.processing_completed = processing_completed

            progress.updated_at = datetime.now(timezone.utc)

            # Update job updated_at
            job = await self.get_job(job_id)
            if job:
                job.updated_at = datetime.now(timezone.utc)

            await self.db.flush()
        except Exception as e:
            logger.debug(f"Job progress update failed for {job_id}", exc_info=e)

    async def update_job_input(self, job_id: UUID, new_input: Dict[str, Any]) -> None:
        """
        Update the input payload of a job.

        :param job_id: Job ID
        :param new_input: New input payload
        """
        job = await self.get_job(job_id)
        if job is None:
            raise FileNotFoundError(f"Job {job_id} not found")

        job.input = to_jsonable(new_input)
        job.updated_at = datetime.now(timezone.utc)

        await self.db.flush()
        logger.info(f"Updated input for job {job_id}")

    async def increment_processed_documents(self, job_id: UUID, delta: int = 1) -> None:
        """
        Increment the number of fully processed documents.

        :param job_id: Job ID
        :param delta: Number to increment by
        """

        now = datetime.now(timezone.utc)

        # This needs to be done first to avoid deadlock with update_job_progress in some rare cases
        await self.db.execute(update(Job).where(Job.job_id == job_id).values(updated_at=now))

        query = (
            update(JobProgress)
            .where(JobProgress.job_id == job_id)
            .values(
                processing_completed=func.coalesce(JobProgress.processing_completed, 0) + delta,
                updated_at=now,
            )
        )
        result = await self.db.execute(query)

        if result.rowcount == 0:
            self.db.add(JobProgress(job_id=job_id, processing_completed=delta, updated_at=now))

        await self.db.flush()

    async def get_job_status(self, job_id: UUID) -> Dict[str, Any]:
        """
        Return a public job status dict.

        :param job_id: Job IDx
        :return: Job status dict
        """
        try:
            job = await self.get_job(job_id)
            if job is None:
                return {"jobId": str(job_id), "status": "not_found"}

            # Get progress
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

            # Add progress details
            if progress:
                progress_dict: Dict[str, Union[str, int]] = {}
                if progress.stage:
                    progress_dict["stage"] = progress.stage
                if progress.message:
                    progress_dict["message"] = progress.message

                # Use different field names based on job type
                if job.job_type == "scrape.getRelevantDocumentation":
                    # Scraper uses iterations (matching IterationProgress schema)
                    if progress.total_processing is not None:
                        progress_dict["totalIterations"] = progress.total_processing
                    if progress.processing_completed is not None:
                        progress_dict["completedIterations"] = progress.processing_completed
                else:
                    # Other jobs use documents
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
        except Exception as e:
            logger.debug(f"Get job status failed for {job_id}", exc_info=e)
            return {}

    async def claim_next_job(self, job_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Atomically claim the next queued job and mark it as running.

        :param job_type: Optional job type filter
        :return: Claimed job record dict or None
        """
        # Query for queued jobs
        query = select(Job).where(Job.status == JobStatus.queued.value).order_by(Job.created_at).limit(1)

        if job_type:
            query = query.where(Job.job_type == job_type)

        result = await self.db.execute(query)
        job = result.scalar_one_or_none()

        if job is None:
            return None

        # Claim it by setting to running
        return await self.set_running(job.job_id)

    async def get_job_status_async(self, job_id: UUID) -> Dict[str, Any]:
        """
        Async version of get_job_status for use in async contexts.

        :param job_id: Job ID
        :return: Job status dict
        """
        return await self.get_job_status(job_id)

    async def get_jobs_by_session(self, session_id: UUID) -> list[Dict[str, Any]]:
        """
        Get all jobs for a given session.

        :param session_id: Session ID
        :return: List of job dicts
        """
        query = select(Job).where(Job.session_id == session_id).order_by(Job.created_at)
        result = await self.db.execute(query)
        jobs = result.scalars().all()

        job_list = []
        for job in jobs:
            job_dict = {
                "jobId": str(job.job_id),
                "type": job.job_type,
                "status": job.status,
                "createdAt": job.created_at.isoformat(),
            }
            if job.updated_at:
                job_dict["updatedAt"] = job.updated_at.isoformat()
            if job.started_at:
                job_dict["startedAt"] = job.started_at.isoformat()
            if job.finished_at:
                job_dict["finishedAt"] = job.finished_at.isoformat()
            job_list.append(job_dict)

        return job_list

    async def recover_stale_running_jobs(self, note: Optional[str] = None) -> int:
        """
        Move all jobs left in 'running' to 'failed'.
        Called on service startup to recover from crashes.

        :param note: Optional message to include in errors
        :return: Number of recovered jobs
        """
        query = select(Job).where(Job.status == JobStatus.running.value)
        result = await self.db.execute(query)
        running_jobs = result.scalars().all()

        count = 0
        message = note or "Recovered at startup: previous process stopped while job was running."

        for job in running_jobs:
            try:
                await self.set_failed(job.job_id, message)
                count += 1
            except Exception as e:
                logger.error(f"Failed to recover job {job.job_id}: {e}")
                continue

        return count

    async def get_not_finished_documentation_jobs_ids(self, session_id: UUID) -> list[UUID]:
        """
        Get IDs of all jobs that interfere with documentation and are not finished yet.

        :param session_id: Session ID
        :return: Sequence of job IDs
        """
        query = select(Job.job_id).where(
            Job.session_id == session_id,
            (Job.job_type == "scrape.getRelevantDocumentation") | (Job.job_type == "documentation.processUpload"),
            (Job.status != JobStatus.finished.value) & (Job.status != JobStatus.failed.value),
        )
        result = await self.db.execute(query)
        job_ids = [job for job in result.scalars().all()]
        return job_ids
