# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio

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
