# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Process-local consumer for the PostgreSQL-backed job queue."""

import asyncio
import logging
import os
import socket
import time
from uuid import uuid4

from src.config import config
from src.core.db import async_session_maker
from src.database.repositories.job_repository import ClaimedJob, JobRepository
from src.jobs.runner import execute_claimed_job

logger = logging.getLogger(__name__)


def build_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:12]}"


class JobWorker:
    """Claim and execute jobs up to the configured per-process concurrency."""

    def __init__(self, worker_id: str | None = None) -> None:
        self.worker_id = worker_id or build_worker_id()
        self._stop_event = asyncio.Event()
        self._coordinator: asyncio.Task[None] | None = None
        self._reaper: asyncio.Task[None] | None = None
        self._active: set[asyncio.Task[None]] = set()

    def start(self) -> None:
        if self._coordinator is not None:
            raise RuntimeError("Job worker is already started")
        self._coordinator = asyncio.create_task(self._run(), name=f"job-worker:{self.worker_id}")
        self._reaper = asyncio.create_task(self._run_reaper(), name=f"job-reaper:{self.worker_id}")
        logger.info(
            "Started database job worker %s with concurrency %s",
            self.worker_id,
            config.jobs.max_concurrent_jobs,
        )

    @property
    def is_running(self) -> bool:
        return (
            self._coordinator is not None
            and not self._coordinator.done()
            and self._reaper is not None
            and not self._reaper.done()
        )

    async def _claim_one(self) -> ClaimedJob | None:
        async with async_session_maker() as db:
            repo = JobRepository(db)
            claimed = await repo.claim_next_job(
                worker_id=self.worker_id,
                claim_timeout_seconds=config.jobs.claim_timeout_seconds,
            )
            await db.commit()
            return claimed

    async def _run_reaper(self) -> None:
        """Run queue repair independently from the high-frequency claim path."""
        while not self._stop_event.is_set():
            try:
                async with async_session_maker() as db:
                    await JobRepository(db).reap_unclaimable_jobs()
                    await db.commit()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Worker %s failed to reap unclaimable jobs", self.worker_id)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=config.jobs.reaper_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            self._active = {task for task in self._active if not task.done()}
            claimed_any = False
            while len(self._active) < config.jobs.max_concurrent_jobs and not self._stop_event.is_set():
                try:
                    claimed = await self._claim_one()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Worker %s failed to claim a job", self.worker_id)
                    break
                if claimed is None:
                    break
                claimed_any = True
                task = asyncio.create_task(
                    execute_claimed_job(claimed),
                    name=f"job:{claimed.job_id}",
                )
                self._active.add(task)
                task.add_done_callback(self._log_task_failure)

            if claimed_any:
                await asyncio.sleep(0)
                continue
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=config.jobs.poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass

    @staticmethod
    def _log_task_failure(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            logger.error("Unhandled job task failure", exc_info=exception)

    async def stop(self) -> None:
        """Stop the worker within one bounded shutdown budget.

        ``shutdown_grace_seconds`` is a single deadline shared by every step:
        draining the coordinator and reaper, letting active jobs finish, and the
        claim release their cancellation performs, for which
        ``claim_release_timeout_seconds`` is reserved at the end. No step waits
        on the database indefinitely, so an unreachable or overloaded database
        can no longer hold the process past its termination grace period. A
        claim that cannot be released in time expires on its own and is requeued
        by the reaper.
        """
        self._stop_event.set()
        deadline = time.monotonic() + config.jobs.shutdown_grace_seconds
        background_tasks = [task for task in (self._coordinator, self._reaper) if task is not None]
        try:
            await self._drain_background_tasks(background_tasks, deadline)
        finally:
            self._coordinator = None
            self._reaper = None
            await self._drain_active_jobs(deadline)
            self._active.clear()
            logger.info("Stopped database job worker %s", self.worker_id)

    async def _drain_background_tasks(self, tasks: list[asyncio.Task[None]], deadline: float) -> None:
        """Let the coordinator and reaper observe the stop signal, then cancel stragglers."""
        if not tasks:
            return
        done, pending = await asyncio.wait(tasks, timeout=max(0.0, deadline - time.monotonic()))
        for task in done:
            if task.cancelled():
                continue
            error = task.exception()
            if error is not None:
                logger.error(
                    "Background task %s failed while stopping worker %s: %s",
                    task.get_name(),
                    self.worker_id,
                    error,
                )
        for task in pending:
            logger.warning(
                "Cancelling background task %s of worker %s that did not stop within the shutdown budget",
                task.get_name(),
                self.worker_id,
            )
            task.cancel()

    async def _drain_active_jobs(self, deadline: float) -> None:
        """Let running jobs finish, then cancel them and bound their claim release."""
        if not self._active:
            return
        job_deadline = deadline - config.jobs.claim_release_timeout_seconds
        _, pending = await asyncio.wait(self._active, timeout=max(0.0, job_deadline - time.monotonic()))
        if not pending:
            return
        logger.warning(
            "Cancelling %s job(s) of worker %s that outlived the shutdown grace period",
            len(pending),
            self.worker_id,
        )
        for task in pending:
            task.cancel()
        _, unfinished = await asyncio.wait(pending, timeout=max(0.0, deadline - time.monotonic()))
        if unfinished:
            logger.error(
                "%s cancelled job(s) of worker %s did not confirm cleanup, their claims expire after %ss",
                len(unfinished),
                self.worker_id,
                config.jobs.claim_timeout_seconds,
            )
