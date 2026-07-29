# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Process-local execution identity used to fence durable job side effects."""

from contextvars import ContextVar, Token
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class JobExecutionContext:
    job_id: UUID
    worker_id: str
    execution_token: UUID


_current_execution: ContextVar[JobExecutionContext | None] = ContextVar(
    "current_job_execution",
    default=None,
)


def get_current_execution() -> JobExecutionContext | None:
    return _current_execution.get()


def set_current_execution(context: JobExecutionContext) -> Token[JobExecutionContext | None]:
    return _current_execution.set(context)


def reset_current_execution(token: Token[JobExecutionContext | None]) -> None:
    _current_execution.reset(token)
