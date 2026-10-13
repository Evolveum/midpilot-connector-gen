# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from src.core.observability.langfuse import langfuse_handler
from src.modules.discovery.schema import RankedLinks
from src.modules.discovery.utils.filter_helpers import rank_candidate_links
from src.modules.discovery.utils.llm_helpers import _parse_search_prompts, generate_queries_via_llm


class _TraceRecorder(BaseCallbackHandler):
    raise_error = True

    def __init__(self):
        self.root_names = []
        self.model_calls = 0

    def on_chain_start(self, serialized, inputs, *, parent_run_id=None, name=None, **kwargs):
        if parent_run_id is None:
            self.root_names.append(name)

    def on_chat_model_start(self, serialized, messages, *, parent_run_id=None, name=None, **kwargs):
        self.model_calls += 1
        if parent_run_id is None:
            self.root_names.append(name)


@pytest.mark.parametrize("needs_repair", [False, True])
def test_query_generation_traces_repair_only_when_needed(needs_repair):
    valid_output = '{"searchPrompts": ["Example REST API"]}'
    model = FakeListChatModel(responses=["invalid JSON" if needs_repair else valid_output])
    parser_model = FakeListChatModel(responses=[valid_output])
    recorder = _TraceRecorder()
    with patch("src.core.observability.langfuse.langfuse_handler", recorder):
        queries, _, parsed = generate_queries_via_llm(
            model=model,
            parser_model=parser_model,
            user_prompt="Find documentation",
            system_prompt="Return search queries",
            num_queries=1,
        )

    assert queries == parsed.search_prompts == ["Example REST API"]
    assert recorder.model_calls == (2 if needs_repair else 1)
    assert recorder.root_names == ["Discovery:Queries:Generate"] + (
        ["Discovery:Queries:ParseRepair"] if needs_repair else []
    )


def test_invalid_repair_keeps_single_attempt_and_surfaces_parsing_error():
    recorder = _TraceRecorder()
    with patch("src.core.observability.langfuse.langfuse_handler", recorder):
        with pytest.raises(OutputParserException):
            _parse_search_prompts(FakeListChatModel(responses=["still invalid"]), "invalid JSON")

    assert recorder.model_calls == 1
    assert recorder.root_names == ["Discovery:Queries:ParseRepair"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
async def test_ranking_preserves_trace_name_single_call_and_fallback(fails):
    urls = ["https://example.com/api", "https://example.com/reference"]
    chain = AsyncMock()
    chain.ainvoke.return_value = RankedLinks(links=list(reversed(urls)))
    if fails:
        chain.ainvoke.side_effect = ValueError("invalid ranking")
    with (
        patch("src.modules.discovery.utils.filter_helpers.get_default_llm"),
        patch("src.modules.discovery.utils.filter_helpers.make_basic_chain", return_value=chain),
    ):
        result = await rank_candidate_links([{"href": url} for url in urls], "Example", "1", max_links=2)

    assert result == (urls if fails else list(reversed(urls)))
    chain.ainvoke.assert_awaited_once()
    llm_config = chain.ainvoke.await_args.kwargs["config"]
    assert llm_config["run_name"] == "Discovery:Links:Rank"
    assert llm_config["callbacks"] == [langfuse_handler]
