# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio

import pytest

from src.core.concurrency import TaskScope


@pytest.mark.asyncio
async def test_scope_cancels_children_when_the_owning_task_is_cancelled() -> None:
    """The regression this scope exists for: detached LLM work outliving its job."""
    child_started = asyncio.Event()
    child_cancelled = asyncio.Event()

    async def long_running_call() -> None:
        child_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            child_cancelled.set()
            raise

    async def owner() -> None:
        async with TaskScope("test-scope") as scope:
            scope.start(long_running_call())
            await asyncio.Event().wait()

    owner_task = asyncio.create_task(owner())
    await child_started.wait()

    owner_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner_task

    assert child_cancelled.is_set()


@pytest.mark.asyncio
async def test_scope_propagates_the_original_error_and_cancels_siblings() -> None:
    """Unlike asyncio.TaskGroup, the scope must not wrap failures in an ExceptionGroup.

    The job runner fences claim loss on the concrete exception type, so wrapping
    would silently break that handling.
    """

    class ClaimLost(Exception):
        pass

    sibling_cancelled = asyncio.Event()

    async def sibling() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            sibling_cancelled.set()
            raise

    with pytest.raises(ClaimLost):
        async with TaskScope("test-scope") as scope:
            scope.start(sibling())
            await asyncio.sleep(0)
            raise ClaimLost("claim taken over")

    assert sibling_cancelled.is_set()


@pytest.mark.asyncio
async def test_aclose_is_idempotent_and_keeps_finished_results() -> None:
    """Callers may close early to stop paid work before a slower cleanup step."""

    async def finished_work() -> str:
        return "processed"

    async with TaskScope("test-scope") as scope:
        task = scope.start(finished_work())
        assert await task == "processed"
        await scope.aclose()
        await scope.aclose()

    assert scope.tasks == (task,)
    assert task.result() == "processed"
