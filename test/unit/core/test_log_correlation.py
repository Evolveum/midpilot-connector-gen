# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
from uuid import uuid4

import pytest

from src.core.job_execution import JobExecutionContext, reset_current_execution, set_current_execution
from src.core.observability.correlation import bind_request_session, current_correlation, reset_request_session
from src.core.observability.logging import LOG_FORMAT, AccessLogPathFilter, CorrelationFilter

MISSING = "--------"


@pytest.fixture
def job_context():
    context = JobExecutionContext(
        job_id=uuid4(),
        session_id=uuid4(),
        job_type="discovery.getCandidateLinks",
        worker_id="host:1:abcdef",
        execution_token=uuid4(),
    )
    token = set_current_execution(context)
    yield context
    reset_current_execution(token)


def _emit() -> str:
    """Filter and format one record the way the configured handler does."""
    record = logging.LogRecord("src.modules.discovery.service", logging.INFO, "", 0, "message", None, None)
    assert CorrelationFilter().filter(record) is True
    return logging.Formatter(LOG_FORMAT).format(record)


def _columns(session: str, job: str) -> str:
    return f"| {session} | {job} |"


def _access_record(path: str, status: int) -> logging.LogRecord:
    atoms = {"m": "GET", "U": path, "s": status}
    return logging.LogRecord("hypercorn.access", logging.INFO, "", 0, "%(m)s %(U)s %(s)s", atoms, None)


def test_record_outside_any_work_gets_placeholders():
    assert _columns(MISSING, MISSING) in _emit()


def test_record_inside_a_job_carries_both_ids(job_context):
    expected = _columns(str(job_context.session_id)[:8], str(job_context.job_id)[:8])

    assert expected in _emit()


def test_record_inside_a_request_carries_the_session_only():
    session_id = uuid4()
    token = bind_request_session(session_id)
    try:
        line = _emit()
    finally:
        reset_request_session(token)

    assert _columns(str(session_id)[:8], MISSING) in line


def test_job_context_wins_over_a_request_binding(job_context):
    """A handler that schedules a job may still be inside its own request scope."""
    token = bind_request_session(uuid4())
    try:
        correlation = current_correlation()
    finally:
        reset_request_session(token)

    assert correlation.session_id == job_context.session_id
    assert correlation.job_id == job_context.job_id


def test_request_binding_is_released_after_reset():
    token = bind_request_session(uuid4())
    reset_request_session(token)

    assert current_correlation().session_id is None


@pytest.mark.asyncio
async def test_correlation_is_inherited_by_spawned_tasks(job_context):
    """Job work runs in tasks the runner does not own; they must stay attributable."""
    expected = _columns(str(job_context.session_id)[:8], str(job_context.job_id)[:8])

    assert expected in await asyncio.create_task(_nested_emit())


async def _nested_emit() -> str:
    return _emit()


def test_access_filter_drops_successful_requests_to_excluded_paths():
    log_filter = AccessLogPathFilter(("/api/v1/scrape",))

    assert log_filter.filter(_access_record("/api/v1/scrape/abc/scrape", 200)) is False


def test_access_filter_keeps_failures_on_excluded_paths():
    log_filter = AccessLogPathFilter(("/api/v1/scrape",))

    assert log_filter.filter(_access_record("/api/v1/scrape/abc/scrape", 500)) is True
    assert log_filter.filter(_access_record("/api/v1/scrape/abc/scrape", 404)) is True


def test_access_filter_keeps_other_paths():
    log_filter = AccessLogPathFilter(("/api/v1/scrape",))

    assert log_filter.filter(_access_record("/api/v1/session/abc", 200)) is True


def test_access_filter_without_configured_paths_keeps_everything():
    log_filter = AccessLogPathFilter(())

    assert log_filter.filter(_access_record("/api/v1/scrape/abc/scrape", 200)) is True


def test_access_filter_keeps_records_it_cannot_interpret():
    log_filter = AccessLogPathFilter(("/api/v1/scrape",))
    record = logging.LogRecord("hypercorn.access", logging.INFO, "", 0, "plain message", None, None)

    assert log_filter.filter(record) is True
    assert log_filter.filter(_access_record("/api/v1/scrape/abc/scrape", "not-a-status")) is True
