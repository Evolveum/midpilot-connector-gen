# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Durable background-job scheduling and execution."""

import asyncio
import inspect
import logging
import time
from collections.abc import Mapping
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple, Union
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import config
from src.core.db import async_session_maker
from src.core.errors import AppError
from src.core.job_execution import (
    JobExecutionContext,
    reset_current_execution,
    set_current_execution,
)
from src.core.observability.llm_metrics import start_llm_usage_tracking, stop_llm_usage_tracking
from src.database.repositories.job_repository import ClaimedJob, JobRepository
from src.database.repositories.session_repository import SessionRepository
from src.jobs import cache, lifecycle, session_persistence
from src.jobs.errors import JobClaimLostError
from src.jobs.payload import (
    build_execution_payload,
    deserialize_call,
    resolve_callable,
    validate_call_arguments,
    validate_execution_payload,
)
from src.shared.enums import JobStage

logger = logging.getLogger(__name__)


async def schedule_coroutine_job(
    *,
    db: AsyncSession,
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
    binary_artifacts: Mapping[str, bytes] | None = None,
) -> UUID:
    """Persist a queued job in the caller's database transaction.

    The API transaction also writes the session's job pointer. A worker can see
    the job only after both records commit, which removes the schedule/pointer
    race present in the former process-local task implementation.
    """
    if dynamic_input_enabled and dynamic_input_provider is None:
        raise ValueError("dynamic_input_provider is required when dynamic_input_enabled is true")
    if await_documentation and await_documentation_timeout is None:
        raise ValueError("await_documentation_timeout is required when await_documentation is true")

    execution_payload = build_execution_payload(
        worker=worker,
        worker_args=tuple(worker_args or ()),
        worker_kwargs=dict(worker_kwargs or {}),
        dynamic_input_provider=dynamic_input_provider if dynamic_input_enabled else None,
        session_result_key=session_result_key,
        await_documentation=await_documentation,
        await_documentation_timeout=await_documentation_timeout,
        binary_artifacts=binary_artifacts,
    )
    repo = JobRepository(db)
    job_id = await repo.create_job(
        input_payload,
        job_type,
        session_id,
        execution_payload=execution_payload,
        binary_artifacts=binary_artifacts,
        documentation_wait_timeout_seconds=await_documentation_timeout if await_documentation else None,
        max_attempts=config.jobs.max_attempts,
    )
    if await_documentation:
        await repo.update_job_progress(
            job_id,
            stage=JobStage.queue,
            message="Waiting for documentation processing to complete.",
        )
    elif initial_stage or initial_message:
        await repo.update_job_progress(job_id, stage=initial_stage, message=initial_message)
    return job_id


async def _resolve_dynamic_input(
    claimed_job: ClaimedJob,
    provider_reference: str,
    input_payload: Dict[str, Any],
) -> Dict[str, Any]:
    provider = resolve_callable(provider_reference)
    async with async_session_maker() as db:
        provider_kwargs: Dict[str, Any] = {"session_id": claimed_job.session_id, "db": db}
        try:
            if "input_payload" in inspect.signature(provider).parameters:
                provider_kwargs["input_payload"] = input_payload
        except (TypeError, ValueError):
            pass

        dynamic_input = await provider(**provider_kwargs)
        if not isinstance(dynamic_input, dict):
            raise TypeError(f"Dynamic input provider {provider_reference} must return a dict")

        job_input = dynamic_input.get("jobInput", {})
        session_input = dynamic_input.get("sessionInput", {})
        if not isinstance(job_input, dict) or not isinstance(session_input, dict):
            raise TypeError(f"Dynamic input provider {provider_reference} returned invalid persistence payloads")

        input_payload.update(job_input)
        repo_job = JobRepository(db)
        await repo_job.update_job_input(
            claimed_job.job_id,
            input_payload,
            worker_id=claimed_job.worker_id,
            execution_token=claimed_job.execution_token,
        )
        # The locked job row fences this session metadata write against a
        # concurrent claim takeover.
        await SessionRepository(db).update_session(claimed_job.session_id, session_input)
        await db.commit()
        return dynamic_input


async def _run_claimed_job(claimed_job: ClaimedJob) -> None:
    payload = validate_execution_payload(claimed_job.execution_payload)
    input_payload = dict(claimed_job.input_payload)

    dynamic_input: Dict[str, Any] = {}
    provider_reference = payload.get("dynamicInputProvider")
    if isinstance(provider_reference, str):
        dynamic_input = await _resolve_dynamic_input(claimed_job, provider_reference, input_payload)

    worker = resolve_callable(str(payload["worker"]))
    async with async_session_maker() as db:
        artifacts = await JobRepository(db).get_job_artifacts(claimed_job.job_id)
    args, kwargs = deserialize_call(
        worker,
        payload["args"],
        payload["kwargs"],
        job_input=input_payload,
        artifacts=artifacts,
    )
    if provider_reference:
        dynamic_args = dynamic_input.get("args", ())
        if not isinstance(dynamic_args, (list, tuple)):
            raise TypeError(f"Dynamic input provider {provider_reference} returned invalid args")
        args += tuple(dynamic_args)

    if "job_id" in inspect.signature(worker).parameters:
        kwargs.setdefault("job_id", claimed_job.job_id)
    validate_call_arguments(worker, args, kwargs)

    async def run_normal_worker() -> Dict[str, Any]:
        result = await worker(*args, **kwargs)
        if hasattr(result, "model_dump"):
            return result.model_dump(by_alias=True, mode="json")  # type: ignore[attr-defined, no-any-return]
        if isinstance(result, dict):
            return result
        return {"value": repr(result)}

    if "scrape" not in claimed_job.job_type and not input_payload.get("skipCache", False):
        result_dict = await cache.reuse_or_run(
            job_type=claimed_job.job_type,
            job_id=claimed_job.job_id,
            session_id=claimed_job.session_id,
            input_payload=input_payload,
            run_normal_worker=run_normal_worker,
        )
    else:
        result_dict = await run_normal_worker()

    session_result_key = payload.get("sessionResultKey")
    if isinstance(session_result_key, str) and session_result_key:
        published_to_session = await session_persistence.persist_result_to_session(
            job_id=claimed_job.job_id,
            session_id=claimed_job.session_id,
            session_result_key=session_result_key,
            result_dict=result_dict,
        )
        if not published_to_session:
            message = (
                f"Result from job {claimed_job.job_id} was not published because the session points to a newer job."
            )
            logger.info(message)
            await lifecycle.append_job_error(claimed_job.job_id, message)

    job_result = result_dict.copy()
    job_result.pop("chunks", None)
    await lifecycle.set_finished(claimed_job.job_id, result=job_result)


async def _heartbeat(claimed_job: ClaimedJob) -> None:
    while True:
        await asyncio.sleep(config.jobs.heartbeat_interval_seconds)
        try:
            async with async_session_maker() as db:
                refreshed = await JobRepository(db).refresh_claim(
                    claimed_job.job_id,
                    worker_id=claimed_job.worker_id,
                    execution_token=claimed_job.execution_token,
                    claim_timeout_seconds=config.jobs.claim_timeout_seconds,
                )
                await db.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to refresh claim for job %s", claimed_job.job_id)
            continue
        if not refreshed:
            raise JobClaimLostError(claimed_job.job_id)


async def _release_claim(claimed_job: ClaimedJob) -> None:
    async with async_session_maker() as db:
        await JobRepository(db).release_claim(
            claimed_job.job_id,
            worker_id=claimed_job.worker_id,
            execution_token=claimed_job.execution_token,
        )
        await db.commit()


async def _release_interrupted_claim(claimed_job: ClaimedJob) -> None:
    """Return an interrupted claim to the queue within a bounded window.

    Executions are cancelled during shutdown, so this must not outlive the
    process grace period. The release therefore runs as a separate task that is
    abandoned once the budget is spent: rolling back and closing a cancelled
    database session can block for as long as the statement it replaces. A claim
    that stays unreleased expires after ``claim_timeout_seconds`` and is requeued
    by the reaper.
    """
    release_task = asyncio.create_task(_release_claim(claimed_job))
    done, _ = await asyncio.wait({release_task}, timeout=config.jobs.claim_release_timeout_seconds)
    if not done:
        release_task.cancel()
        logger.error(
            "Timed out after %ss returning interrupted job %s to the queue, its claim expires after %ss",
            config.jobs.claim_release_timeout_seconds,
            claimed_job.job_id,
            config.jobs.claim_timeout_seconds,
        )
        return
    error = release_task.exception()
    if error is not None:
        logger.error(
            "Failed to return interrupted job %s to the queue",
            claimed_job.job_id,
            exc_info=error,
        )


async def execute_claimed_job(claimed_job: ClaimedJob) -> None:
    """Execute one claimed job while heartbeating and fencing all writes."""
    context_token = set_current_execution(
        JobExecutionContext(
            job_id=claimed_job.job_id,
            session_id=claimed_job.session_id,
            job_type=claimed_job.job_type,
            worker_id=claimed_job.worker_id,
            execution_token=claimed_job.execution_token,
        )
    )
    llm_usage, usage_token = start_llm_usage_tracking()
    started = time.monotonic()
    outcome = "interrupted"
    logger.info("Started job %s (attempt %s)", claimed_job.job_type, claimed_job.attempt_count)
    execution_task = asyncio.create_task(_run_claimed_job(claimed_job))
    heartbeat_task = asyncio.create_task(_heartbeat(claimed_job))
    try:
        done, _ = await asyncio.wait(
            {execution_task, heartbeat_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if execution_task in done:
            await execution_task
        else:
            heartbeat_task.result()
            await execution_task
        outcome = "succeeded"
    except asyncio.CancelledError:
        execution_task.cancel()
        heartbeat_task.cancel()
        await asyncio.gather(execution_task, heartbeat_task, return_exceptions=True)
        await _release_interrupted_claim(claimed_job)
        raise
    except JobClaimLostError:
        outcome = "claim lost"
        execution_task.cancel()
        await asyncio.gather(execution_task, return_exceptions=True)
        logger.warning("Stopped stale execution after the job claim was lost")
    except Exception as exc:
        outcome = "failed"
        if isinstance(exc, AppError) and exc.status_code < 500:
            # An expected client/domain outcome, not a crash: the message is the
            # whole story, and a stack trace would only bury it in the log.
            logger.error("Job failed: %s", exc)
        else:
            logger.exception("Job failed during execution")
        try:
            await lifecycle.set_failed(claimed_job.job_id, error=str(exc))
        except JobClaimLostError:
            logger.warning("Could not fail the job because its claim was already lost")
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        elapsed = time.monotonic() - started
        logger.info(
            "Finished job %s: %s in %.1fs (%s)",
            claimed_job.job_type,
            outcome,
            elapsed,
            llm_usage.describe(elapsed),
        )
        stop_llm_usage_tracking(usage_token)
        reset_current_execution(context_token)
