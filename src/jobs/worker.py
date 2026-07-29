# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Process-local consumer for the PostgreSQL-backed job queue."""

import asyncio
import logging
import os
import socket
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
        try:
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
        except asyncio.CancelledError:
            raise

    @staticmethod
    def _log_task_failure(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            logger.error("Unhandled job task failure", exc_info=exception)

    async def stop(self) -> None:
        self._stop_event.set()
        background_tasks = [task for task in (self._coordinator, self._reaper) if task is not None]
        try:
            if background_tasks:
                results = await asyncio.gather(*background_tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                        logger.error(
                            "Background coordinator failed while stopping worker %s: %s",
                            self.worker_id,
                            result,
                        )
        finally:
            self._coordinator = None
            self._reaper = None
            if self._active and config.jobs.shutdown_grace_seconds > 0:
                _, pending = await asyncio.wait(
                    self._active,
                    timeout=config.jobs.shutdown_grace_seconds,
                )
            else:
                pending = set(self._active)

            for task in pending:
                task.cancel()
            if self._active:
                await asyncio.gather(*self._active, return_exceptions=True)
            self._active.clear()
            logger.info("Stopped database job worker %s", self.worker_id)
