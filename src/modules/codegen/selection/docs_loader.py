# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from importlib import resources

logger = logging.getLogger(__name__)


def read_adoc_text(package: str, filename: str) -> str:
    """
    Read .adoc documentation file from package data using importlib.resources.
    Works in dev and when packaged (wheel/zip).
    """
    try:
        with resources.files(package).joinpath(filename).open("r", encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        logger.exception("Could not read resource %s/%s", package, filename)
        return ""
