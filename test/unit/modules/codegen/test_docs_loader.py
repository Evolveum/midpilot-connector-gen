# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import pytest

from src.modules.codegen.selection.docs_loader import load_required_adoc_text


def test_load_required_adoc_text_raises_for_missing_resource() -> None:
    with pytest.raises(FileNotFoundError, match="Required codegen documentation resource not found"):
        load_required_adoc_text("src.modules.codegen.documentations.rest", "missing-doc.adoc")
