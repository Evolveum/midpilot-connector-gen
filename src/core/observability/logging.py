# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Application logging configuration."""

import logging
import sys
from typing import Any, Iterable, Mapping
from uuid import UUID

from colorlog import ColoredFormatter

from src.config import config
from src.core.observability.correlation import current_correlation

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(process)d | %(session)s | %(job)s | %(name)s | %(message)s"
COLOR_LOG_FORMAT = f"%(log_color)s{LOG_FORMAT}"

# Correlation ids are truncated to keep log lines readable. The full uuid is always
# available on the "Created job ..." record that opens a job's life.
_CORRELATION_ID_LENGTH = 8
_MISSING_CORRELATION_ID = "-" * _CORRELATION_ID_LENGTH

# Successful responses below this status are eligible for access-log exclusion;
# client and server errors are always logged, whatever the path.
_ACCESS_LOG_ERROR_STATUS = 400


def get_configured_log_level_name() -> str:
    """Return the configured logging level in stdlib/Hypercorn format."""
    return config.logging.level.value.upper()


class CorrelationFilter(logging.Filter):
    """Attach the current session and job ids to every record.

    Records with no attributable work (startup, shutdown, unauthenticated
    requests) get a placeholder so the log stays column-aligned.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        correlation = current_correlation()
        record.session = _shorten(correlation.session_id)
        record.job = _shorten(correlation.job_id)
        return True


def _shorten(correlation_id: UUID | None) -> str:
    return _MISSING_CORRELATION_ID if correlation_id is None else str(correlation_id)[:_CORRELATION_ID_LENGTH]


class AccessLogPathFilter(logging.Filter):
    """Drop successful requests to configured paths from the access log.

    Status polling and documentation endpoints are requested continuously and
    would otherwise bury the application log. Only successful responses are
    dropped: a failure on an excluded path is still worth seeing.
    """

    def __init__(self, excluded_paths: tuple[str, ...]) -> None:
        super().__init__()
        self._excluded_paths = excluded_paths

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._excluded_paths:
            return True

        atoms = record.args
        if not isinstance(atoms, Mapping):
            return True

        path = atoms.get("U")
        if not isinstance(path, str) or not any(excluded in path for excluded in self._excluded_paths):
            return True

        return not _is_successful_status(atoms.get("s"))


def _is_successful_status(status: Any) -> bool:
    try:
        return int(status) < _ACCESS_LOG_ERROR_STATUS
    except (TypeError, ValueError):
        # An unparseable status is an anomaly in itself - keep the record.
        return False


def setup_logging() -> logging.Logger:
    """
    Configure application logging and return the prepared Hypercorn error logger.

    Hypercorn accepts a ``logging.Logger`` instance for ``Config.errorlog``. Passing
    this logger prevents Hypercorn from replacing its handlers and propagation
    settings when its logging facade is initialized lazily.
    """
    level = logging.getLevelNamesMapping()[get_configured_log_level_name()]

    # Base logger config
    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    root_logger = logging.getLogger()

    if config.logging.colors:
        color_formatter = ColoredFormatter(
            COLOR_LOG_FORMAT,
            datefmt=None,
            reset=True,
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "red,bg_white",
            },
        )
        for handler in root_logger.handlers:
            handler.setFormatter(color_formatter)

    _install_filter(root_logger.handlers, CorrelationFilter())

    hypercorn_access = logging.getLogger("hypercorn.access")
    hypercorn_error = logging.getLogger("hypercorn.error")

    hypercorn_access.setLevel(level)
    _install_filter([hypercorn_access], AccessLogPathFilter(tuple(config.logging.access_log_excluded_paths)))
    hypercorn_error.setLevel(level)
    hypercorn_error.handlers.clear()
    hypercorn_error.propagate = True

    return hypercorn_error


def _install_filter(targets: Iterable[logging.Filterer], log_filter: logging.Filter) -> None:
    """Add a filter to each target, replacing any earlier instance of its type.

    ``logging.Filterer`` is the base of both ``Logger`` and ``Handler`` and is what
    owns ``filters`` - the only capability this needs from a target.

    ``setup_logging`` runs once per process, but tests and reloads may call it
    again; without the replacement the same filter would stack up.
    """
    filter_type = type(log_filter)
    for target in targets:
        target.filters = [existing for existing in target.filters if not isinstance(existing, filter_type)]
        target.addFilter(log_filter)
