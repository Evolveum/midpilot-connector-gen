# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import Mock, patch

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from src.config import config
from src.config.llm import ReasoningEffort
from src.core.llm import build_structured_chain, get_default_llm, make_basic_chain
from src.integrations.web.link_classification import _link_filter_reasoning_effort


def _set_reasoning_effort(monkeypatch, value: ReasoningEffort | None) -> None:
    monkeypatch.setattr(config.llm, "reasoning_effort", value)


def test_get_default_llm_uses_configured_reasoning_effort(monkeypatch):
    _set_reasoning_effort(monkeypatch, "high")

    with patch("src.core.llm.ChatOpenAI") as chat_openai:
        get_default_llm()

    assert chat_openai.call_args.kwargs["reasoning_effort"] == "high"


def test_get_default_llm_explicit_none_disables_reasoning_effort(monkeypatch):
    _set_reasoning_effort(monkeypatch, "high")

    with patch("src.core.llm.ChatOpenAI") as chat_openai:
        get_default_llm(reasoning_effort=None)

    assert "reasoning_effort" not in chat_openai.call_args.kwargs


def test_irrelevant_links_reasoning_effort_uses_medium_only_when_global_reasoning_is_enabled(monkeypatch):
    _set_reasoning_effort(monkeypatch, None)
    assert _link_filter_reasoning_effort() is None

    _set_reasoning_effort(monkeypatch, "high")
    assert _link_filter_reasoning_effort() == "medium"


def test_build_structured_chain_uses_provided_llm_and_partial_variables() -> None:
    class _Response(BaseModel):
        value: str

    llm = Mock()

    with (
        patch("src.core.llm.get_default_llm") as get_default,
        patch("src.core.llm.make_basic_chain", return_value=Mock()) as make_chain,
    ):
        build_structured_chain(
            "system {extra}",
            "user",
            _Response,
            llm=llm,
            partial_variables={"extra": "context"},
            user_role="human",
        )

    get_default.assert_not_called()
    prompt = make_chain.call_args.args[0]
    assert make_chain.call_args.args[1] is llm
    assert prompt.partial_variables["extra"] == "context"
    assert "format_instructions" in prompt.partial_variables


@pytest.mark.asyncio
async def test_structured_chain_recovers_invalid_escaped_apostrophes_without_llm_retry() -> None:
    class _Response(BaseModel):
        value: str

    invalid_json = r"""{"value":"Slack field \'Username\' maps to userName"}"""
    llm = FakeListChatModel(responses=[invalid_json])
    parser: PydanticOutputParser[_Response] = PydanticOutputParser(pydantic_object=_Response)
    prompt = ChatPromptTemplate.from_messages([("user", "Return JSON")])
    chain = make_basic_chain(prompt, llm, parser)  # type: ignore[arg-type]

    result = await chain.ainvoke({})

    assert result == _Response(value="Slack field 'Username' maps to userName")
