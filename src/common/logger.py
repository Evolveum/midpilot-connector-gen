# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
import sys

from colorlog import ColoredFormatter

from src.config import config


def setup_logging():
    """
    Configure the root logger and Hypercorn loggers based on application settings.
    """
    level = getattr(logging, config.logging.level.value.upper(), logging.INFO)

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
    # Unlike its access logger, Hypercorn's error logger propagates to the root logger by
    # default, which would double-log every message through both its own stderr handler
    # and ours.
    hypercorn_error.propagate = False
