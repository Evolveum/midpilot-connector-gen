#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple, Union
from uuid import UUID

from .database.config import async_session_maker
from .database.repositories.job_repository import JobRepository
from .database.repositories.session_repository import SessionRepository
from .enums import JobStage

logger = logging.getLogger(__name__)


def update_job_progress(
    job_id: UUID,
    *,
    stage: Optional[Union[str, JobStage]] = None,
    message: Optional[str] = None,
    total_processing: Optional[int] = None,
    processing_completed: Optional[int] = None,
) -> None:
    """Update progress information for a running job."""

    async def _update() -> None:
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

    try:
        asyncio.create_task(_update())
    except RuntimeError:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_update())
            else:
                loop.run_until_complete(_update())
        except Exception as ex:
            logger.debug(f"Job progress update failed - no event loop: {ex}")


# def increment_processed_documents(job_id: UUID, delta: int = 1) -> None:
#     """
#     Increment the number of fully processed documents.
#     """
#
#     async def _increment() -> None:
#         try:
#             async with async_session_maker() as db:
#                 repo = JobRepository(db)
#                 await repo.increment_processed_documents(job_id, delta)
#                 await db.commit()
#         except Exception as e:
#             logger.debug("Increment processed documents failed.", exc_info=e)
#
#     try:
#         asyncio.create_task(_increment())
#     except RuntimeError:
#         try:
#             loop = asyncio.get_event_loop()
#             if loop.is_running():
#                 loop.create_task(_increment())
#             else:
#                 loop.run_until_complete(_increment())
#         except Exception as ex:
#             logger.debug(f"Increment processed documents failed - no event loop: {ex}")


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
            return data
    except Exception as e:
        logger.error("Set job to failed failed.", exc_info=e)
        raise


async def get_job_status(job_id: UUID) -> Dict[str, Any]:
    """Return a public job status dict."""
    try:
        async with async_session_maker() as db:
            repo = JobRepository(db)
            return await repo.get_job_status(job_id)
    except Exception as e:
        logger.debug("Get a job failed.", exc_info=e)
        return {"jobId": str(job_id), "status": "not_found"}


async def schedule_coroutine_job(
    *,
    job_type: str,
    input_payload: Dict[str, Any],
    worker: Callable[..., Awaitable[Any]],
    worker_args: Optional[Tuple[Any, ...]] = None,
    worker_kwargs: Optional[Dict[str, Any]] = None,
    initial_stage: Optional[Union[str, JobStage]] = None,
    initial_message: Optional[str] = None,
    session_id: UUID,
    session_result_key: Optional[str] = None,
) -> UUID:
    """
    Create a job record and schedule `worker` coroutine to process it in background.
    The worker must accept the job_id as the last positional argument or via kwarg `job_id` if desired.

    If session_result_key is provided, the result will be automatically
    stored in the session under the given key when the job completes.

    :param session_id: Required session ID for the job
    """

    # Create job in database
    job_id = await create_job(input_payload, job_type, session_id)
    if initial_stage or initial_message:
        update_job_progress(job_id, stage=initial_stage, message=initial_message)

    async def _runner() -> None:
        try:
            await set_running(job_id)

            args = tuple(worker_args or ())
            kwargs = dict(worker_kwargs or {})

            # Prefer explicit kwarg if caller wants to pass it
            if "job_id" in worker.__code__.co_varnames:  # type: ignore[attr-defined]
                kwargs.setdefault("job_id", job_id)

            result = await worker(*args, **kwargs)

            # Auto-serialize result
            result_dict: Dict[str, Any]
            if hasattr(result, "model_dump"):
                result_dict = result.model_dump(by_alias=True, mode="json")  # type: ignore[attr-defined]
            elif isinstance(result, dict):
                result_dict = result
            else:
                result_dict = {"value": repr(result)}

            # Store result in session if requested (before saving to job)
            if session_result_key:
                try:
                    async with async_session_maker() as db:
                        repo = SessionRepository(db)

                        # Handle new format with chunks metadata
                        if (
                            isinstance(result_dict, dict)
                            and "result" in result_dict
                            and ("relevant_chunk_indices" in result_dict or "relevantChunks" in result_dict)
                        ):
                            # New format: store result and relevant chunks separately
                            actual_result = result_dict["result"]
                            # Support both old format (relevant_chunk_indices) and new format (relevant_chunks)
                            relevant_chunks = result_dict.get("relevantChunks") or result_dict.get(
                                "relevant_chunk_indices", []
                            )

                            session_updates = {session_result_key: actual_result}

                            # Store relevant chunks for this specific extraction
                            if relevant_chunks:
                                # Get existing relevant_chunks dict or create new one
                                existing_relevant = await repo.get_session_data(session_id, "relevantChunks") or {}
                                existing_relevant[session_result_key] = relevant_chunks
                                session_updates["relevantChunks"] = existing_relevant

                            await repo.update_session(session_id, session_updates)
                        else:
                            await repo.update_session(session_id, {session_result_key: result_dict})
                        await db.commit()
                except Exception as e:
                    logger.error(f"Failed to update session {session_id} after job {job_id}: {e}")
                    # Don't fail the job if session update fails
                    pass

            # Prepare job result (exclude large chunks array, keep only metadata)
            job_result_dict = result_dict.copy() if isinstance(result_dict, dict) else result_dict
            if isinstance(job_result_dict, dict) and "chunks" in job_result_dict:
                # Remove the large chunks array from job result, keep metadata
                del job_result_dict["chunks"]
                # metadata already contains summary info about chunks

            await set_finished(job_id, result=job_result_dict)
        except asyncio.CancelledError as cancel_exc:  # graceful cancellation (e.g., shutdown)
            try:
                await set_failed(job_id, error=f"Job cancelled/interrupted: {cancel_exc}")
            except Exception:
                pass
            raise
        except Exception as exc:
            await set_failed(job_id, error=str(exc))

    asyncio.create_task(_runner())
    return job_id


def append_job_error(job_id: UUID, message: str) -> None:
    """
    Append a non-fatal error message to the job record without changing its status.
    Used to surface partial/chunk errors while allowing the job to finish successfully.
    """

    async def _append() -> None:
        try:
            async with async_session_maker() as db:
                repo = JobRepository(db)
                await repo.append_job_error(job_id, message)
                await db.commit()
        except Exception as e:
            logger.debug(f"Append job error failed for {job_id}", exc_info=e)

    try:
        asyncio.create_task(_append())
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
