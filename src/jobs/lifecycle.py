# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Job lifecycle operations: thin async wrappers over :class:`JobRepository`.

These functions own terminal state transitions, progress updates, and status
reads. Worker-originated writes are automatically
fenced by the execution identity stored in the current context.
"""

import logging
from typing import Any, Dict, Optional, Union
from uuid import UUID

from src.core.db import async_session_maker
from src.core.job_execution import get_current_execution
from src.database.repositories.job_repository import JobRepository
from src.jobs.errors import JobClaimLostError
from src.jobs.result_envelope import strip_internal_result_fields
from src.shared.enums import JobStage

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
            execution = get_current_execution()
            await repo.update_job_progress(
                job_id,
                stage=stage,
                message=message,
                total_processing=total_processing,
                processing_completed=processing_completed,
                worker_id=execution.worker_id if execution else None,
                execution_token=execution.execution_token if execution else None,
            )
            await db.commit()
    except Exception as e:
        if isinstance(e, JobClaimLostError):
            raise
        logger.warning("Job progress update failed for %s", job_id, exc_info=e)


async def increment_processed_documents(job_id: UUID, delta: int = 1) -> None:
    try:
        async with async_session_maker() as db:
            repo = JobRepository(db)
            execution = get_current_execution()
            await repo.increment_processed_documents(
                job_id,
                delta,
                worker_id=execution.worker_id if execution else None,
                execution_token=execution.execution_token if execution else None,
            )
            await db.commit()
    except JobClaimLostError:
        raise
    except Exception:
        # Progress accounting must not turn an otherwise valid LLM result into
        # a failed job during a transient observability/database incident.
        logger.warning("Failed to increment progress for job %s", job_id, exc_info=True)


async def set_finished(job_id: UUID, result: Dict[str, Any]) -> Dict[str, Any]:
    """Transition a running job to finished state, attach `result`, and return the record."""
    try:
        async with async_session_maker() as db:
            repo = JobRepository(db)
            execution = get_current_execution()
            if execution is None:
                raise RuntimeError("Finishing a job requires an active execution context")
            data = await repo.finish_claimed_job(
                job_id,
                result,
                worker_id=execution.worker_id,
                execution_token=execution.execution_token,
            )
            if data is None:
                raise JobClaimLostError(job_id)
            await db.commit()

        return data
    except Exception as e:
        logger.error("Set job to finished failed.", exc_info=e)
        raise


async def set_failed(job_id: UUID, error: str) -> Dict[str, Any]:
    """Transition a job to failed state with a normalized list of error messages."""
    try:
        async with async_session_maker() as db:
            repo = JobRepository(db)
            execution = get_current_execution()
            if execution is None:
                raise RuntimeError("Failing a job requires an active execution context")
            data = await repo.fail_claimed_job(
                job_id,
                error,
                worker_id=execution.worker_id,
                execution_token=execution.execution_token,
            )
            if data is None:
                raise JobClaimLostError(job_id)
            await db.commit()

        return data
    except Exception as e:
        logger.error("Set job to failed failed.", exc_info=e)
        raise


async def _append_job_error_now(job_id: UUID, message: str) -> None:
    async with async_session_maker() as db:
        repo = JobRepository(db)
        execution = get_current_execution()
        await repo.append_job_error(
            job_id,
            message,
            worker_id=execution.worker_id if execution else None,
            execution_token=execution.execution_token if execution else None,
        )
        await db.commit()


async def get_job_status(job_id: UUID | None) -> Dict[str, Any]:
    """Return a public job status dict."""
    if job_id is None:
        return {"jobId": None, "status": "not_found"}
    async with async_session_maker() as db:
        repo = JobRepository(db)
        status = await repo.get_job_status(job_id)
        if "result" in status:
            status["result"] = strip_internal_result_fields(status["result"])
        return status


async def append_job_error(job_id: UUID, message: str) -> None:
    """
    Append a non-fatal error message to the job record without changing its status.
    Used to surface partial/chunk errors while allowing the job to finish successfully.
    """

    try:
        await _append_job_error_now(job_id, message)
    except JobClaimLostError:
        raise
    except Exception:
        logger.exception("Append job error failed for %s", job_id)
