#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

import logging

from src.common.logger import setup_logging


def test_logger_output(caplog):
    # Initialize logging configuration
    setup_logging()

    logger = logging.getLogger("test_logger")

    # Set log level to DEBUG for the test (optional, depends on your setup)
    logger.setLevel(logging.WARN)

    with caplog.at_level(logging.DEBUG):
        logger.debug("Debug message")
        logger.info("Info message")
        logger.warning("Warning message")
        logger.error("Error message")
        logger.critical("Critical message")

    # Check that expected messages are in the captured logs
    assert "Debug message" not in caplog.text
    assert "Info message" not in caplog.text
    assert "Warning message" in caplog.text
    assert "Error message" in caplog.text
    assert "Critical message" in caplog.text
