# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Structured concurrency primitives for long-running background work."""

import asyncio
import logging
from types import TracebackType
from typing import Any, Coroutine, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class TaskScope:
    """Own the lifetime of every task started through it."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._tasks: list[asyncio.Task[Any]] = []

    def start(self, coro: Coroutine[Any, Any, T], *, name: str | None = None) -> asyncio.Task[T]:
        """Start a task whose lifetime is bound to this scope."""
        task = asyncio.create_task(coro, name=name)
        self._tasks.append(task)
        return task

    @property
    def tasks(self) -> tuple[asyncio.Task[Any], ...]:
        return tuple(self._tasks)

    async def aclose(self) -> None:
        """Cancel and reap the tasks still running in this scope.

        Idempotent, so a caller that wants to stop its tasks earlier than scope
        exit - to stop paid work before a slower cleanup step - can call this
        directly.
        """
        pending = [task for task in self._tasks if not task.done()]
        if not pending:
            return
        logger.warning("Cancelling %s unfinished task(s) of scope %s", len(pending), self.name)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    async def __aenter__(self) -> "TaskScope":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()
