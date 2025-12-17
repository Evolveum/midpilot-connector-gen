# Copyright (c) 2025 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
import sys

from colorlog import ColoredFormatter

from src.config import config


def setup_logging():
    """
    Configure the root logger and Uvicorn loggers based on application settings.
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

    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_error = logging.getLogger("uvicorn.error")

    uvicorn_access.setLevel(level)
    uvicorn_error.setLevel(level)
