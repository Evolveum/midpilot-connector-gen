# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""In-process registry of background job tasks and their completion futures.

This is intentionally process-local. Cross-worker / durable waiting is a separate
architectural concern (see the production-readiness review) and is not handled here.
"""

import asyncio
from typing import Any, Awaitable, List
from uuid import UUID

_job_futures: dict[UUID, asyncio.Future] = {}
_background_tasks: set[asyncio.Task] = set()


def spawn_background_task(coro: Awaitable[Any]) -> asyncio.Task:
    """Schedule a coroutine as a tracked background task to prevent premature GC."""
    task = asyncio.ensure_future(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def register_future(job_id: UUID) -> asyncio.Future:
    """Register and return a completion future for the given job."""
    future = asyncio.get_event_loop().create_future()
    _job_futures[job_id] = future
    return future


def resolve_future(job_id: UUID) -> None:
    """Resolve and remove the completion future for a finished/failed job, if present."""
    future = _job_futures.pop(job_id, None)
    if future and not future.done():
        future.set_result(None)


def futures_for(job_ids: List[UUID]) -> List[asyncio.Future]:
    """Return the registered futures for the given job ids that are still tracked."""
    return [_job_futures[jid] for jid in job_ids if jid in _job_futures]
