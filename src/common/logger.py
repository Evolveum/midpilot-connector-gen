# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
import sys

from colorlog import ColoredFormatter

from src.config import config


def get_configured_log_level_name() -> str:
    """Return the configured logging level in stdlib/Hypercorn format."""
    return config.logging.level.value.upper()


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
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    root_logger = logging.getLogger()

    if config.logging.colors:
        color_formatter = ColoredFormatter(
            "%(log_color)s%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
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

    hypercorn_access = logging.getLogger("hypercorn.access")
    hypercorn_error = logging.getLogger("hypercorn.error")

    hypercorn_access.setLevel(level)
    hypercorn_error.setLevel(level)
    hypercorn_error.handlers.clear()
    hypercorn_error.propagate = True

    return hypercorn_error
