# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
import re
from functools import cache
from importlib import resources

from src.modules.codegen.schema import OperationAssets

logger = logging.getLogger(__name__)


@cache
def load_required_adoc_text(package: str, filename: str) -> str:
    """
    Read a required .adoc documentation file from package data using importlib.resources.
    Works in dev and when packaged (wheel/zip).
    """
    try:
        with resources.files(package).joinpath(filename).open("r", encoding="utf-8") as fh:
            return fh.read()
    except Exception as exc:
        logger.exception("[Codegen:Documentation] Could not read resource %s/%s", package, filename)
        raise FileNotFoundError(f"Required codegen documentation resource not found: {package}/{filename}") from exc


def select_adoc_sections(text: str, sections: tuple[str, ...]) -> str:
    """Keep the introduction and requested sections, including nested subsections.

    Fail on missing or ambiguous headings: an upstream reorganization must be
    reviewed rather than silently depriving the model of its DSL reference.
    """
    if not sections:
        return text
    headings = list(re.finditer(r"^(={2,}) (.+)$", text, re.MULTILINE))
    selected = [text[: headings[0].start()]] if headings else []
    ranges = []
    for name in sections:
        matches = [i for i, heading in enumerate(headings) if heading[2] == name]
        if len(matches) != 1:
            raise ValueError(f"Expected one documentation section {name!r}, found {len(matches)}")
        index = matches[0]
        heading = headings[index]
        end = next((h.start() for h in headings[index + 1 :] if len(h[1]) <= len(heading[1])), len(text))
        ranges.append((heading.start(), end))
    previous_end = -1
    for start, end in sorted(ranges):
        if start >= previous_end:
            selected.append(text[start:end])
            previous_end = end
    return "\n".join(selected)


@cache
def load_operation_documentation(assets: OperationAssets) -> tuple[str, str]:
    """One cached assembly shared by every generation operation; call off the event loop."""
    package = "src.modules.codegen.documentations"
    primary = select_adoc_sections(load_required_adoc_text(package, assets.docs_path), assets.docs_sections)
    docs = "\n\n".join([primary, *(load_required_adoc_text(package, path) for path in assets.additional_docs_paths)])
    declarative = select_adoc_sections(
        load_required_adoc_text(package, assets.declarative_docs_path), assets.declarative_sections
    )
    return docs, declarative
