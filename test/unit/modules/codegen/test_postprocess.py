# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from types import SimpleNamespace

from src.modules.codegen.utils.postprocess import coerce_llm_text, strip_markdown_fences


def test_coerce_llm_text_extracts_string_content():
    assert coerce_llm_text(SimpleNamespace(content="generated code")) == "generated code"


def test_coerce_llm_text_preserves_fallback_stringification():
    assert coerce_llm_text(123) == "123"
    assert coerce_llm_text(None) == ""


def test_strip_markdown_fences_removes_outer_fence_only():
    fenced = '```groovy\nobjectClass("User") {\n}\n```'

    assert strip_markdown_fences(fenced) == 'objectClass("User") {\n}'
