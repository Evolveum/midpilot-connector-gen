# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Resolution of the correlation identity attached to every log record.

Work reaches the log from two directions and both must be attributable:

* a background job, which already publishes its full identity through
  :mod:`src.core.job_execution`;
* an HTTP request, which knows only the session it addresses.

This module is the single place that decides which of the two applies, so the
logging filter stays a formatter and call sites never repeat the ids by hand.
"""

from contextvars import ContextVar, Token
from dataclasses import dataclass
from uuid import UUID

from src.core.job_execution import get_current_execution

_request_session_id: ContextVar[UUID | None] = ContextVar("request_session_id", default=None)


@dataclass(frozen=True)
class Correlation:
    """The ids identifying the work that produced a log record."""

    session_id: UUID | None
    job_id: UUID | None


def bind_request_session(session_id: UUID) -> Token[UUID | None]:
    """Attribute the current request's log records to a session."""
    return _request_session_id.set(session_id)


def reset_request_session(token: Token[UUID | None]) -> None:
    """Release the request's session attribution."""
    _request_session_id.reset(token)


def current_correlation() -> Correlation:
    """Return the identity of the work in progress.

    A running job wins over the request scope: a job carries both ids and is the
    more specific context, and the two can legitimately overlap when a request
    handler schedules work that starts before the response is written.
    """
    execution = get_current_execution()
    if execution is not None:
        return Correlation(session_id=execution.session_id, job_id=execution.job_id)
    return Correlation(session_id=_request_session_id.get(), job_id=None)
