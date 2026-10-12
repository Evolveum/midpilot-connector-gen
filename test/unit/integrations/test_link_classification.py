# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch

import pytest

from src.core.observability.langfuse import langfuse_handler
from src.integrations.web import IrrelevantLinks, classify_irrelevant_links


@pytest.mark.asyncio
async def test_shared_link_filter_preserves_trace_name_and_retry_budget():
    chain = AsyncMock()
    chain.ainvoke.side_effect = [RuntimeError("504 Gateway Timeout")] * 3
    with (
        patch("src.integrations.web.link_classification.get_default_llm"),
        patch("src.integrations.web.link_classification.make_basic_chain", return_value=chain),
        patch("src.core.llm.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        with pytest.raises(RuntimeError, match="504 Gateway Timeout"):
            await classify_irrelevant_links(("system", "links"))

    configs = [call.kwargs["config"] for call in chain.ainvoke.await_args_list]
    assert [item["run_name"] for item in configs] == ["Web:Links:ClassifyIrrelevant"] * 3
    assert all(item["callbacks"] == [langfuse_handler] for item in configs)
    assert [call.args[0] for call in sleep.await_args_list] == [1.0, 2.0]


@pytest.mark.asyncio
async def test_shared_link_filter_returns_result_without_extra_calls():
    chain = AsyncMock()
    result = IrrelevantLinks(links=["https://example.com/about"])
    chain.ainvoke.return_value = result
    with (
        patch("src.integrations.web.link_classification.get_default_llm"),
        patch("src.integrations.web.link_classification.make_basic_chain", return_value=chain),
    ):
        assert await classify_irrelevant_links(("system", "links")) is result
    chain.ainvoke.assert_awaited_once()
