# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import patch

import pytest
from langchain_core.runnables import RunnableConfig

from src.config import config
from src.core.errors import LLMUnavailableError
from src.core.observability.langfuse import langfuse_handler
from src.integrations.web.page_links import PageLink
from src.modules.scrape.core.assessment import assess_page, select_links


# PyCharm's monkeypatch inspection does not resolve pydantic model fields as attribute names
# (mypy resolves them correctly), so the field-name string arguments are suppressed here once.
# noinspection PyUnresolvedReferences
def _set_chunk_llm_retry(monkeypatch, *, attempts: int, base_delay_seconds: float) -> None:
    monkeypatch.setattr(config.scrape_and_process, "chunk_llm_retry_attempts", attempts)
    monkeypatch.setattr(config.scrape_and_process, "chunk_llm_retry_base_delay_seconds", base_delay_seconds)


class _FailingChain:
    def __init__(self, exc: Exception):
        self._exc = exc
        self.calls = 0
        self.configs: list[RunnableConfig] = []

    async def ainvoke(self, *args, **kwargs):
        self.calls += 1
        self.configs.append(kwargs["config"])
        raise self._exc


def _assess():
    return assess_page(
        url="https://docs.example.test/users", title="Users", content="Users", app="Example", version=None
    )


def _select():
    return select_links([PageLink("https://docs.example.test/groups", "Groups")], app="Example", version=None)


@pytest.mark.asyncio
@pytest.mark.parametrize(("call", "run_name"), [(_assess, "Scrape:Pages:Assess"), (_select, "Scrape:Links:Select")])
async def test_scrape_decisions_raise_when_llm_unreachable(monkeypatch, call, run_name):
    """A connection outage during a scrape decision must fail the scrape, not be recorded as a page error."""
    _set_chunk_llm_retry(monkeypatch, attempts=2, base_delay_seconds=0)

    chain = _FailingChain(Exception("Connection error."))
    with (
        patch("src.modules.scrape.core.assessment.get_default_llm"),
        patch("src.modules.scrape.core.assessment.make_basic_chain", return_value=chain),
    ):
        with pytest.raises(LLMUnavailableError):
            await call()

    assert chain.calls == 2  # retried before failing
    assert [item["run_name"] for item in chain.configs] == [run_name] * 2
    assert all(item["callbacks"] == [langfuse_handler] for item in chain.configs)


@pytest.mark.asyncio
@pytest.mark.parametrize("call", [_assess, _select])
async def test_scrape_decisions_raise_other_errors_unchanged_without_retry(monkeypatch, call):
    """The scraper records a non-outage failure on the job; it is neither retried nor hidden here."""
    _set_chunk_llm_retry(monkeypatch, attempts=2, base_delay_seconds=0)

    chain = _FailingChain(ValueError("unparseable output"))
    with (
        patch("src.modules.scrape.core.assessment.get_default_llm"),
        patch("src.modules.scrape.core.assessment.make_basic_chain", return_value=chain),
    ):
        with pytest.raises(ValueError, match="unparseable output"):
            await call()

    assert chain.calls == 1
