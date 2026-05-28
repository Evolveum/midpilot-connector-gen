# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import patch

from src.common.llm import get_default_llm
from src.config import config
from src.modules.scrape.llms import _get_irrelevant_links_reasoning_effort


def test_get_default_llm_uses_configured_reasoning_effort(monkeypatch):
    monkeypatch.setattr(config.llm, "reasoning_effort", "high")

    with patch("src.common.llm.ChatOpenAI") as chat_openai:
        get_default_llm()

    assert chat_openai.call_args.kwargs["reasoning_effort"] == "high"


def test_get_default_llm_explicit_none_disables_reasoning_effort(monkeypatch):
    monkeypatch.setattr(config.llm, "reasoning_effort", "high")

    with patch("src.common.llm.ChatOpenAI") as chat_openai:
        get_default_llm(reasoning_effort=None)

    assert "reasoning_effort" not in chat_openai.call_args.kwargs


def test_irrelevant_links_reasoning_effort_uses_medium_only_when_global_reasoning_is_enabled(monkeypatch):
    monkeypatch.setattr(config.llm, "reasoning_effort", None)
    assert _get_irrelevant_links_reasoning_effort() is None

    monkeypatch.setattr(config.llm, "reasoning_effort", "high")
    assert _get_irrelevant_links_reasoning_effort() == "medium"
