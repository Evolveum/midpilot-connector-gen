# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Background job orchestration.

:func:`schedule_coroutine_job` creates a job record and spawns a background coroutine that
optionally waits for documentation jobs, resolves dynamic input, reuses cached output,
runs the worker, persists the result to the session, and finalizes the job state.
"""

import asyncio
import inspect
import logging
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple, Union
from uuid import UUID

from src.common.database.config import async_session_maker
from src.common.database.repositories.job_repository import JobRepository
from src.common.database.repositories.session_repository import SessionRepository
from src.common.enums import JobStage
from src.common.jobs import cache, futures, lifecycle, session_persistence

logger = logging.getLogger(__name__)


async def schedule_coroutine_job(
    *,
    job_type: str,
    input_payload: Dict[str, Any],
    dynamic_input_enabled: bool = False,
    dynamic_input_provider: Optional[Callable[..., Awaitable[Any]]] = None,
    worker: Callable[..., Awaitable[Any]],
    worker_args: Optional[Tuple[Any, ...]] = None,
    worker_kwargs: Optional[Dict[str, Any]] = None,
    initial_stage: Optional[Union[str, JobStage]] = None,
    initial_message: Optional[str] = None,
    session_id: UUID,
    session_result_key: Optional[str] = None,
    await_documentation: bool = False,
    await_documentation_timeout: Optional[float] = None,
) -> UUID:
    """
    Create a job record and schedule `worker` coroutine to process it in background.
    The worker must accept the job_id as the last positional argument or via kwarg `job_id` if desired.

    If session_result_key is provided, the result will be automatically
    stored in the session under the given key when the job completes.

    :param session_id: Required session ID for the job
    """

    # Create job in database
    job_id = await lifecycle.create_job(input_payload, job_type, session_id)

    if initial_stage or initial_message:
        await lifecycle.update_job_progress(job_id, stage=initial_stage, message=initial_message)

    futures.register_future(job_id)

    async def _runner() -> None:
        try:
            if await_documentation:
                await lifecycle.update_job_progress(
                    job_id, stage="queue", message="Waiting for documentation processing to complete."
                )
                async with async_session_maker() as db:
                    repo_doc = JobRepository(db)
                    not_finished_jobs_ids = await repo_doc.get_not_finished_documentation_jobs_ids(session_id)
                    if not_finished_jobs_ids:
                        pending = futures.futures_for(not_finished_jobs_ids)
                        if pending:
                            try:
                                await asyncio.wait_for(asyncio.gather(*pending), timeout=await_documentation_timeout)
                            except asyncio.TimeoutError:
                                logger.warning(f"Job {job_id} timed out waiting for documentation jobs to complete")

            dynamic_input = {}
            if dynamic_input_enabled and dynamic_input_provider:
                async with async_session_maker() as db:
                    provider_kwargs: Dict[str, Any] = {"session_id": session_id, "db": db}
                    accepts_input_payload = False
                    try:
                        accepts_input_payload = "input_payload" in inspect.signature(dynamic_input_provider).parameters
                    except (TypeError, ValueError):
                        pass
                    if accepts_input_payload:
                        provider_kwargs["input_payload"] = input_payload
                    dynamic_input = await dynamic_input_provider(**provider_kwargs)
                    # Update Job input in Jobs table
                    input_payload.update(dynamic_input.get("jobInput", {}))
                    repo_job = JobRepository(db)
                    await repo_job.update_job_input(job_id, input_payload)
                    # Update operation input in Sessions table
                    repo_session = SessionRepository(db)
                    await repo_session.update_session(session_id, dynamic_input.get("sessionInput", {}))
                    await db.commit()

            await lifecycle.set_running(job_id)
            args = tuple(worker_args or ())

            if dynamic_input_enabled and dynamic_input_provider:
                args += dynamic_input.get("args", ())

            kwargs = dict(worker_kwargs or {})

            # Prefer explicit kwarg if caller wants to pass it
            if "job_id" in worker.__code__.co_varnames:  # type: ignore[attr-defined]
                kwargs.setdefault("job_id", job_id)

            async def run_normal_worker() -> Dict[str, Any]:
                result = await worker(*args, **kwargs)
                # Auto-serialize result
                if hasattr(result, "model_dump"):
                    return result.model_dump(by_alias=True, mode="json")  # type: ignore[attr-defined, no-any-return]
                if isinstance(result, dict):
                    return result
                return {"value": repr(result)}

            # TODO: implement caching also for codegen, move scraper implementation here
            if "scrape" not in job_type and not input_payload.get("skipCache", False):
                result_dict = await cache.reuse_or_run(
                    job_type=job_type,
                    job_id=job_id,
                    session_id=session_id,
                    input_payload=input_payload,
                    run_normal_worker=run_normal_worker,
                )
            else:
                result_dict = await run_normal_worker()

            # Store result in session if requested (before saving to job)
            if session_result_key:
                await session_persistence.persist_result_to_session(
                    job_id=job_id,
                    session_id=session_id,
                    session_result_key=session_result_key,
                    result_dict=result_dict,
                    input_payload=input_payload,
                )

            # Prepare job result (exclude large chunks array, keep only metadata)
            job_result_dict = result_dict.copy() if isinstance(result_dict, dict) else result_dict
            if isinstance(job_result_dict, dict) and "chunks" in job_result_dict:
                # Remove the large chunks array from job result, keep metadata
                del job_result_dict["chunks"]
                # metadata already contains summary info about chunks

            await lifecycle.set_finished(job_id, result=job_result_dict)
        except asyncio.CancelledError as cancel_exc:  # graceful cancellation (e.g., shutdown)
            try:
                await lifecycle.set_failed(job_id, error=f"Job cancelled/interrupted: {cancel_exc}")
            except Exception:
                pass
            raise
        except Exception as exc:
            await lifecycle.set_failed(job_id, error=str(exc))

    futures.spawn_background_task(_runner())
    return job_id
