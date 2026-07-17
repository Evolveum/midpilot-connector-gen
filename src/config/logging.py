# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from enum import Enum

from pydantic import BaseModel


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
    :param colors: Enable or disable colored log output.
    :param live_reload: Enable live reloading of logs on code changes.
    """

    level: LogLevel = LogLevel.info
    access_log: bool = True
    colors: bool = False
    live_reload: bool = False
