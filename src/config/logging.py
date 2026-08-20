# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from enum import Enum
from typing import List

from pydantic import BaseModel, Field


class LogLevel(str, Enum):
    """
    Log level settings for the application.

    :cvar debug: Debug-level logging, most verbose.
    :cvar info: Informational messages, default level.
    :cvar warning: Warning messages, potential issues.
    :cvar error: Error messages, serious problems.
    :cvar critical: Critical errors, application shutdown scenarios.
    """

    debug = "debug"
    info = "info"
    warning = "warning"
    error = "error"
    critical = "critical"


class LoggingSettings(BaseModel):
    """
    Configuration for application logging.

    :param level: LogLevel enum specifying the logging threshold.
    :param access_log: Enable or disable access logs.
    :param access_log_excluded_paths: Request paths omitted from the access log when they succeed.
    :param colors: Enable or disable colored log output.
    :param live_reload: Enable live reloading of logs on code changes.
    """

    level: LogLevel = LogLevel.info
    access_log: bool = True
    access_log_excluded_paths: List[str] = Field(
        default_factory=lambda: ["/docs", "/redoc", "/openapi.json", "/favicon.ico"],
        description=(
            "Substrings of request paths whose successful responses are omitted from the access log. "
            "Failed responses are always logged. Add the job-status polling endpoints here when their "
            "per-client poll interval makes the access log unreadable."
        ),
    )
    colors: bool = False
    live_reload: bool = False
