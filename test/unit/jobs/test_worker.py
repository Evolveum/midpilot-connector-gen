# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
from contextlib import suppress

import pytest

from src.config import config
from src.jobs.worker import JobWorker


@pytest.mark.asyncio
async def test_stop_drains_active_jobs_even_when_coordinator_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = JobWorker(worker_id="test-worker")

    async def fail_coordinator() -> None:
        raise RuntimeError("coordinator failed")

    async def run_forever() -> None:
        await asyncio.Event().wait()

    worker._coordinator = asyncio.create_task(fail_coordinator())
    active = asyncio.create_task(run_forever())
    worker._active.add(active)
    monkeypatch.setattr(config.jobs, "shutdown_grace_seconds", 0)

    await worker.stop()

    assert active.cancelled()
    assert worker._active == set()
    assert worker._coordinator is None


@pytest.mark.asyncio
async def test_stop_returns_within_budget_when_job_cleanup_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled job whose claim release blocks must not hold the process."""
    worker = JobWorker(worker_id="test-worker")
    monkeypatch.setattr(config.jobs, "shutdown_grace_seconds", 0.05)
    monkeypatch.setattr(config.jobs, "claim_release_timeout_seconds", 0.02)
    cleanup_started = asyncio.Event()

    async def job_with_unreachable_database() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup_started.set()
            await asyncio.Event().wait()

    active = asyncio.create_task(job_with_unreachable_database())
    worker._active.add(active)

    # The bound is asserted by the timeout: an unbounded wait would hang here.
    await asyncio.wait_for(worker.stop(), timeout=5)

    assert cleanup_started.is_set()
    assert not active.done()
    assert worker._active == set()

    active.cancel()
    with suppress(asyncio.CancelledError):
        await active


@pytest.mark.asyncio
async def test_stop_cancels_background_task_that_never_observes_the_stop_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reaper blocked on an advisory lock must not delay the shutdown either."""
    worker = JobWorker(worker_id="test-worker")
    monkeypatch.setattr(config.jobs, "shutdown_grace_seconds", 0.05)
    monkeypatch.setattr(config.jobs, "claim_release_timeout_seconds", 0.02)

    async def block_on_the_database() -> None:
        await asyncio.Event().wait()

    reaper = asyncio.create_task(block_on_the_database(), name="job-reaper:test-worker")
    worker._reaper = reaper

    await asyncio.wait_for(worker.stop(), timeout=5)

    assert worker._reaper is None
    with suppress(asyncio.CancelledError):
        await asyncio.wait_for(reaper, timeout=1)
    assert reaper.cancelled()
