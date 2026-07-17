# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import patch

import pytest

from src.common.errors import LLMUnavailableError
from src.config import config
from src.modules.scrape.core.llms import get_relevant_links_from_text


class _FailingChain:
    def __init__(self, exc: Exception):
        self._exc = exc
        self.calls = 0

    async def ainvoke(self, *args, **kwargs):
        self.calls += 1
        raise self._exc


@pytest.mark.asyncio
async def test_relevant_links_raises_when_llm_unreachable(monkeypatch):
    """A connection outage during link extraction must fail the scrape, not silently return None."""
    monkeypatch.setattr(config.scrape_and_process, "chunk_llm_retry_attempts", 2)
    monkeypatch.setattr(config.scrape_and_process, "chunk_llm_retry_base_delay_seconds", 0)

    chain = _FailingChain(Exception("Connection error."))
    with (
        patch("src.modules.scrape.core.llms.get_default_llm"),
        patch("src.modules.scrape.core.llms.make_basic_chain", return_value=chain),
    ):
        with pytest.raises(LLMUnavailableError):
            await get_relevant_links_from_text(("developer {parser_instructions}", "user"))

    assert chain.calls == 2  # retried before failing


@pytest.mark.asyncio
async def test_relevant_links_returns_none_on_non_outage_error(monkeypatch):
    """A one-off, non-connectivity error skips the page's link suggestions without failing the job."""
    monkeypatch.setattr(config.scrape_and_process, "chunk_llm_retry_attempts", 2)
    monkeypatch.setattr(config.scrape_and_process, "chunk_llm_retry_base_delay_seconds", 0)

    chain = _FailingChain(ValueError("unparseable output"))
    with (
        patch("src.modules.scrape.core.llms.get_default_llm"),
        patch("src.modules.scrape.core.llms.make_basic_chain", return_value=chain),
    ):
        result = await get_relevant_links_from_text(("developer {parser_instructions}", "user"))

    assert result is None
    assert chain.calls == 1  # non-transient errors are not retried
