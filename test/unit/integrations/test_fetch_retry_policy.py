# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from types import SimpleNamespace

import pytest

from src.integrations.web.fetch import _is_retryable_failure


def _result(status_code=None, error_message="", response_headers=None):
    return SimpleNamespace(status_code=status_code, error_message=error_message, response_headers=response_headers)


@pytest.mark.parametrize("status_code", [408, 425, 429, 500, 503, None])
def test_transient_failures_are_retried(status_code) -> None:
    assert _is_retryable_failure(_result(status_code=status_code)) is True


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 410])
def test_permanent_client_errors_are_not_retried(status_code) -> None:
    assert _is_retryable_failure(_result(status_code=status_code)) is False


def test_robots_denial_is_not_retried() -> None:
    result = _result(
        status_code=403,
        error_message="Access denied by robots.txt",
        response_headers={"X-Robots-Status": "Blocked by robots.txt"},
    )
    assert _is_retryable_failure(result) is False
