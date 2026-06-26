# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Job lifecycle operations: thin async wrappers over :class:`JobRepository`.

These functions own job state transitions (create/running/finished/failed),
progress updates and status reads. They wrap the persistence layer and keep the
in-process completion futures (see :mod:`src.common.jobs.futures`) in sync.
"""

import asyncio
import logging
from typing import Any, Dict, Optional, Union
from uuid import UUID

from src.common.database.config import async_session_maker
from src.common.database.repositories.job_repository import JobRepository
from src.common.enums import JobStage
from src.common.jobs import futures

logger = logging.getLogger(__name__)


async def update_job_progress(
    job_id: UUID,
    *,
    stage: Optional[Union[str, JobStage]] = None,
    message: Optional[str] = None,
    total_processing: Optional[int] = None,
    processing_completed: Optional[int] = None,
) -> None:
    """Update progress information for a running job."""
    try:
        async with async_session_maker() as db:
            repo = JobRepository(db)
            await repo.update_job_progress(
                job_id,
                stage=stage,
                message=message,
                total_processing=total_processing,
                processing_completed=processing_completed,
            )
            await db.commit()
    except Exception as e:
        logger.debug("Job progress update failed", exc_info=e)


async def increment_processed_documents(job_id: UUID, delta: int = 1) -> None:
    async with async_session_maker() as db:
        repo = JobRepository(db)
        await repo.increment_processed_documents(job_id, delta)
        await db.commit()


async def create_job(input_payload: Dict[str, Any], job_type: str, session_id: UUID) -> UUID:
    """Create a queued job and return job_id."""
    try:
        async with async_session_maker() as db:
            repo = JobRepository(db)
            job_id = await repo.create_job(input_payload, job_type, session_id)
            await db.commit()
            return job_id
    except Exception as e:
        logger.error("Create job failed.", exc_info=e)
        raise


async def set_running(job_id: UUID) -> Dict[str, Any]:
    """Transition a queued job to running state and return the updated job record."""
    try:
        async with async_session_maker() as db:
            repo = JobRepository(db)
            data = await repo.set_running(job_id)
            await db.commit()
            return data
    except Exception as e:
        logger.debug("Set job running failed.", exc_info=e)
        return {}


async def set_finished(job_id: UUID, result: Dict[str, Any]) -> Dict[str, Any]:
    """Transition a running job to finished state, attach `result`, and return the record."""
    try:
        async with async_session_maker() as db:
            repo = JobRepository(db)
            data = await repo.set_finished(job_id, result)
            await db.commit()

        futures.resolve_future(job_id)

        return data
    except Exception as e:
        logger.error("Set job to finished failed.", exc_info=e)
        raise


async def set_failed(job_id: UUID, error: str) -> Dict[str, Any]:
    """Transition a job to failed state with a normalized list of error messages."""
    try:
        async with async_session_maker() as db:
            repo = JobRepository(db)
            data = await repo.set_failed(job_id, error)
            await db.commit()

        futures.resolve_future(job_id)

        return data
    except Exception as e:
        logger.error("Set job to failed failed.", exc_info=e)
        raise


async def _append_job_error_now(job_id: UUID, message: str) -> None:
    async with async_session_maker() as db:
        repo = JobRepository(db)
        await repo.append_job_error(job_id, message)
        await db.commit()


async def get_job_status(job_id: UUID | None) -> Dict[str, Any]:
    """Return a public job status dict."""
    if job_id is None:
        return {"jobId": None, "status": "not_found"}
    try:
        async with async_session_maker() as db:
            repo = JobRepository(db)
            return await repo.get_job_status(job_id)
    except Exception as e:
        logger.debug("Get a job failed.", exc_info=e)
        return {"jobId": str(job_id), "status": "not_found"}


def append_job_error(job_id: UUID, message: str) -> None:
    """
    Append a non-fatal error message to the job record without changing its status.
    Used to surface partial/chunk errors while allowing the job to finish successfully.
    """

    async def _append() -> None:
        try:
            await _append_job_error_now(job_id, message)
        except Exception as e:
            logger.debug(f"Append job error failed for {job_id}", exc_info=e)

    try:
        futures.spawn_background_task(_append())
    except RuntimeError:
        # No running loop - try to get or create one
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_append())
            else:
                loop.run_until_complete(_append())
        except Exception as ex:
            logger.debug(f"Append job error failed - no event loop: {ex}")


async def recover_stale_running_jobs(note: Optional[str] = None) -> int:
    """
    Move all jobs left in 'running' to 'failed'.
    This is intended to be called on service startup to recover from crashes or hard stops (e.g., CTRL+C).

    :param note: Optional message to include in the error list.
    :return: number of recovered jobs.
    """
    try:
        async with async_session_maker() as db:
            repo = JobRepository(db)
            count = await repo.recover_stale_running_jobs(note)
            await db.commit()
            return count
    except Exception as e:
        logger.error(f"Failed to recover stale running jobs: {e}")
        return 0
