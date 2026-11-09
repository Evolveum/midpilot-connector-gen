# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
from contextlib import suppress
from datetime import datetime

import pytest

from src.config import config
from src.jobs.worker import JobWorker


def _idle_background_loops(monkeypatch: pytest.MonkeyPatch, worker: JobWorker) -> None:
    async def idle() -> None:
        await worker._stop_event.wait()

    monkeypatch.setattr(worker, "_run", idle)
    monkeypatch.setattr(worker, "_run_reaper", idle)


@pytest.mark.asyncio
async def test_session_retention_is_not_started_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = JobWorker(worker_id="test-worker")
    monkeypatch.setattr(config.jobs, "session_retention_days", 0)
    _idle_background_loops(monkeypatch, worker)

    worker.start()
    assert worker._retention is None
    assert worker.is_running

    await asyncio.wait_for(worker.stop(), timeout=5)


@pytest.mark.asyncio
async def test_session_retention_deletes_in_batches_until_a_short_batch_and_stops_with_the_worker(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    worker = JobWorker(worker_id="test-worker")
    monkeypatch.setattr(config.jobs, "session_retention_days", 30)
    _idle_background_loops(monkeypatch, worker)
    batches = iter([20, 3])
    cutoffs: list[datetime] = []
    swept = asyncio.Event()

    async def delete_batch(cutoff: datetime) -> int:
        cutoffs.append(cutoff)
        deleted = next(batches, 0)
        if deleted < 20:
            swept.set()
        return deleted

    monkeypatch.setattr(worker, "_delete_inactive_session_batch", delete_batch)

    with caplog.at_level(logging.INFO, logger="src.jobs.worker"):
        worker.start()
        await asyncio.wait_for(swept.wait(), timeout=5)
        assert worker.is_running
        await asyncio.wait_for(worker.stop(), timeout=5)

    assert len(cutoffs) == 2
    assert cutoffs[0] == cutoffs[1]
    assert worker._retention is None
    assert "[Jobs:Retention] Deleted 23 sessions inactive for more than 30 days" in caplog.text


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
async def test_stop_reserves_the_release_budget_when_a_background_task_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A coordinator stuck on the database must not spend the claim-release window."""
    worker = JobWorker(worker_id="test-worker")
    monkeypatch.setattr(config.jobs, "shutdown_grace_seconds", 0.2)
    monkeypatch.setattr(config.jobs, "claim_release_timeout_seconds", 0.1)
    claim_released = asyncio.Event()

    async def block_on_the_database() -> None:
        await asyncio.Event().wait()

    async def job_releasing_its_claim_on_cancel() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0.01)
            claim_released.set()

    worker._coordinator = asyncio.create_task(block_on_the_database(), name="job-worker:test-worker")
    active = asyncio.create_task(job_releasing_its_claim_on_cancel())
    worker._active.add(active)

    await asyncio.wait_for(worker.stop(), timeout=5)

    assert claim_released.is_set()


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
