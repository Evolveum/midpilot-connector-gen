# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.modules.scrape.core.scraper import scraper_loop
from src.modules.scrape.schema import RelevantLinks

PAGE_URL = "https://docs.example.test/api"


async def _scrape_urls(_links: list[str]):
    yield SimpleNamespace(
        url=PAGE_URL,
        markdown=SimpleNamespace(
            fit_markdown="Users API",
            references_markdown="## References\n⟨1⟩ https://docs.example.test/users: Users\n",
            markdown_with_citations="Users⟨1⟩",
        ),
    )


def _slow_clean_documentation_links(documentation_references, link_arr, _documentation_url):
    time.sleep(0.3)
    return documentation_references, link_arr


@pytest.mark.asyncio
async def test_link_cleanup_does_not_block_the_event_loop() -> None:
    """Health checks and status polls must be answered while a scrape processes a page's links.

    Regression: the link cleanup ran on the event loop and took seconds on link-heavy pages, so the
    readiness probe failed and Gravitee timed out polls with a 504.
    """
    largest_gap = 0.0
    scraping_done = asyncio.Event()

    async def measure_loop_gaps() -> None:
        nonlocal largest_gap
        previous = time.perf_counter()
        while not scraping_done.is_set():
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            largest_gap = max(largest_gap, now - previous)
            previous = now

    ticker = asyncio.create_task(measure_loop_gaps())
    # Let the ticker take its first timestamp; the mocked awaits below never yield to the loop.
    await asyncio.sleep(0)
    with (
        patch(
            "src.modules.scrape.core.scraper.get_all_content_types",
            new_callable=AsyncMock,
            return_value={PAGE_URL: "text/html"},
        ),
        patch(
            "src.modules.scrape.core.scraper.scrape_all_data_documentations",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch("src.modules.scrape.core.scraper.scrape_urls", _scrape_urls),
        patch("src.modules.scrape.core.scraper._clean_documentation_links", _slow_clean_documentation_links),
        patch(
            "src.modules.scrape.core.scraper.filter_out_irrelevant_links",
            new_callable=AsyncMock,
            return_value=([], ["https://docs.example.test/users"]),
        ),
        patch(
            "src.modules.scrape.core.scraper.get_relevant_links_from_text",
            new_callable=AsyncMock,
            return_value=RelevantLinks(links=["https://docs.example.test/users"]),
        ),
    ):
        try:
            new_links = await scraper_loop(links_to_scrape=[PAGE_URL], app="Example", app_version="1")
        finally:
            scraping_done.set()
            await ticker

    assert new_links == ["https://docs.example.test/users"]
    assert largest_gap < 0.15
