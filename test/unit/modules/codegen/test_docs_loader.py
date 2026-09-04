# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from importlib import resources
from unittest.mock import patch

import pytest

from src.modules.codegen.selection.docs_loader import load_required_adoc_text

_REST_DOCS_PACKAGE = "src.modules.codegen.documentations.rest"
_EXISTING_DOC = "50-relationship.adoc"


@pytest.fixture(autouse=True)
def _clear_docs_cache():
    load_required_adoc_text.cache_clear()
    yield
    load_required_adoc_text.cache_clear()


def test_load_required_adoc_text_raises_for_missing_resource() -> None:
    with pytest.raises(FileNotFoundError, match="Required codegen documentation resource not found"):
        load_required_adoc_text(_REST_DOCS_PACKAGE, "missing-doc.adoc")


def test_load_required_adoc_text_reads_each_resource_once() -> None:
    """Packaged references are immutable, and every codegen job pulls the same ones."""
    with patch(
        "src.modules.codegen.selection.docs_loader.resources.files",
        wraps=resources.files,
    ) as spy:
        first = load_required_adoc_text(_REST_DOCS_PACKAGE, _EXISTING_DOC)
        second = load_required_adoc_text(_REST_DOCS_PACKAGE, _EXISTING_DOC)

    assert first == second
    assert first.strip()
    spy.assert_called_once_with(_REST_DOCS_PACKAGE)


def test_load_required_adoc_text_keeps_retrying_a_missing_resource() -> None:
    """``cache`` stores return values, not exceptions: a missing file must not be
    remembered as a permanent failure that outlives a fixed deployment."""
    with patch(
        "src.modules.codegen.selection.docs_loader.resources.files",
        side_effect=FileNotFoundError("boom"),
    ) as spy:
        for _ in range(2):
            with pytest.raises(FileNotFoundError):
                load_required_adoc_text(_REST_DOCS_PACKAGE, "missing-doc.adoc")

    assert spy.call_count == 2
