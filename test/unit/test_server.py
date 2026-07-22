# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from server import build_hypercorn_config
from src.config.app import AppSettings


def test_hypercorn_uses_prepared_error_logger(caplog):
    hypercorn_config = build_hypercorn_config()

    assert isinstance(hypercorn_config.errorlog, logging.Logger)
    assert hypercorn_config.errorlog.handlers == []
    assert hypercorn_config.errorlog.propagate is True

    message = "prepared Hypercorn logger"
    with caplog.at_level(logging.INFO, logger="hypercorn.error"):
        asyncio.run(hypercorn_config.log.info(message))

    matching_records = [record for record in caplog.records if record.message == message]
    assert len(matching_records) == 1
    assert hypercorn_config.log.error_logger is hypercorn_config.errorlog


@pytest.mark.parametrize("workers", [0, -1])
def test_app_settings_reject_non_positive_workers(workers):
    with pytest.raises(ValidationError):
        AppSettings(workers=workers)


@pytest.mark.parametrize(
    ("workers", "live_reload", "expected_hypercorn_workers"),
    [
        (1, False, 0),
        (1, True, 1),
        (2, False, 2),
        (2, True, 2),
    ],
)
def test_hypercorn_worker_mapping(workers, live_reload, expected_hypercorn_workers):
    app_settings = AppSettings(workers=workers, live_reload=live_reload)

    with patch("server.config.app", app_settings):
        hypercorn_config = build_hypercorn_config()

    assert hypercorn_config.workers == expected_hypercorn_workers
