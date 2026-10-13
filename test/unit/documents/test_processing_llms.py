# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch

import pytest

from src.config import config
from src.core.observability.langfuse import langfuse_handler
from src.documents.processing.llms import get_llm_processed_chunk
from src.documents.processing.schema import LlmChunkOutput


@pytest.mark.asyncio
async def test_chunk_processing_preserves_trace_name_across_transient_retry(monkeypatch):
    monkeypatch.setattr(config.scrape_and_process, "chunk_llm_retry_attempts", 2)
    monkeypatch.setattr(config.scrape_and_process, "chunk_llm_retry_base_delay_seconds", 0)
    result = LlmChunkOutput(
        summary="API docs", num_endpoints=1, tags=["endpoints"], category=config.scrape_and_process.chunk_categories[0]
    )
    chain = AsyncMock()
    chain.ainvoke.side_effect = [RuntimeError("504 Gateway Timeout"), result]
    with (
        patch("src.documents.processing.llms.get_default_llm"),
        patch("src.documents.processing.llms.make_basic_chain", return_value=chain),
    ):
        assert await get_llm_processed_chunk(("system", "document")) is result

    configs = [call.kwargs["config"] for call in chain.ainvoke.await_args_list]
    assert [item["run_name"] for item in configs] == ["Scrape:Chunks:Process"] * 2
    assert all(item["callbacks"] == [langfuse_handler] for item in configs)
