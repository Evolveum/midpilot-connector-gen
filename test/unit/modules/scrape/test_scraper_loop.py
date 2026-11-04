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
PAGE_HTML = "<html><body><main><h1>Users API</h1><p>Lists users.</p></main></body></html>"


def _page(html: str = PAGE_HTML, *, url: str = PAGE_URL, link_urls: tuple[str, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(
        url=url,
        html=html,
        links={"internal": [{"href": link_url} for link_url in link_urls]},
        markdown=SimpleNamespace(
            references_markdown="## References\n⟨1⟩ https://docs.example.test/users: Users\n",
            markdown_with_citations="Users⟨1⟩",
        ),
    )


async def _scrape_urls(_links: list[str], **_options):
    yield _page()


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


def _scrape_urls_returning(*pages: SimpleNamespace):
    async def _scrape(_links: list[str], **_options):
        for page in pages:
            yield page

    return _scrape


def _loop_patches(html: str, relevant_links_mock: AsyncMock):
    return (
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
        patch("src.modules.scrape.core.scraper.scrape_urls", _scrape_urls_returning(_page(html))),
        patch(
            "src.modules.scrape.core.scraper.filter_out_irrelevant_links",
            new_callable=AsyncMock,
            return_value=([], ["https://docs.example.test/users"]),
        ),
        patch("src.modules.scrape.core.scraper.get_relevant_links_from_text", relevant_links_mock),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "html",
    ["", "<html><body>  </body></html>", "<html><body><nav><a href='/users'>Users</a></nav></body></html>"],
)
async def test_page_without_main_content_is_not_saved_but_its_links_are_followed(html: str) -> None:
    scheduled: list[str] = []

    async def on_documentation_scraped(documentation) -> None:
        scheduled.append(documentation.url)

    relevant_links = AsyncMock(return_value=RelevantLinks(links=["https://docs.example.test/users"]))
    content_types, data_docs, scrape, link_filter, select = _loop_patches(html, relevant_links)
    with content_types, data_docs, scrape, link_filter, select:
        new_links = await scraper_loop(
            links_to_scrape=[PAGE_URL],
            app="Example",
            app_version="1",
            on_documentation_scraped=on_documentation_scraped,
        )

    assert scheduled == []
    assert new_links == ["https://docs.example.test/users"]


@pytest.mark.asyncio
async def test_last_iteration_reports_remaining_links_without_llm_selection() -> None:
    scheduled: list[str] = []

    async def on_documentation_scraped(documentation) -> None:
        scheduled.append(documentation.url)

    relevant_links = AsyncMock(return_value=RelevantLinks(links=[]))
    content_types, data_docs, scrape, link_filter, select = _loop_patches(PAGE_HTML, relevant_links)
    with content_types, data_docs, scrape, link_filter, select:
        new_links = await scraper_loop(
            links_to_scrape=[PAGE_URL],
            app="Example",
            app_version="1",
            last_iteration=True,
            on_documentation_scraped=on_documentation_scraped,
        )

    relevant_links.assert_not_awaited()
    assert scheduled == [PAGE_URL]
    assert new_links == ["https://docs.example.test/users"]


OPENAPI_JSON = '{"openapi": "3.0.3", "info": {"title": "Example", "version": "1"}, "paths": {"/users": {}}}'
PACKAGE_JSON = '{"name": "docs-portal", "dependencies": {"react": "^18"}}'
DATA_URL = "https://docs.example.test/files/data.json"


async def _no_pages(_links: list[str], **_options):
    return
    yield


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "starter", "spec_format"),
    [
        (OPENAPI_JSON, False, "openapi"),
        (PACKAGE_JSON, True, None),
        ("# Users\n\nRaw markdown documentation of the users endpoint.", False, None),
    ],
)
async def test_data_files_that_are_documentation_are_saved(content: str, starter: bool, spec_format) -> None:
    scheduled = []

    async def on_documentation_scraped(documentation) -> None:
        scheduled.append(documentation)

    with (
        patch(
            "src.modules.scrape.core.scraper.get_all_content_types",
            new_callable=AsyncMock,
            return_value={DATA_URL: "application/json"},
        ),
        patch(
            "src.modules.scrape.core.scraper.scrape_all_data_documentations",
            new_callable=AsyncMock,
            return_value=[(DATA_URL, content)],
        ),
        patch("src.modules.scrape.core.scraper.scrape_urls", _no_pages),
    ):
        await scraper_loop(
            links_to_scrape=[DATA_URL],
            app="Example",
            app_version="1",
            on_documentation_scraped=on_documentation_scraped,
            starter_links=[DATA_URL] if starter else [],
        )

    assert [documentation.url for documentation in scheduled] == [DATA_URL]
    retrieval = scheduled[0].retrieval
    assert retrieval.starter is starter
    assert (retrieval.spec.format if retrieval.spec else None) == spec_format


@pytest.mark.asyncio
async def test_crawled_json_that_is_no_api_description_is_not_saved() -> None:
    scheduled = []

    async def on_documentation_scraped(documentation) -> None:
        scheduled.append(documentation)

    with (
        patch(
            "src.modules.scrape.core.scraper.get_all_content_types",
            new_callable=AsyncMock,
            return_value={DATA_URL: "application/json"},
        ),
        patch(
            "src.modules.scrape.core.scraper.scrape_all_data_documentations",
            new_callable=AsyncMock,
            return_value=[(DATA_URL, PACKAGE_JSON)],
        ),
        patch("src.modules.scrape.core.scraper.scrape_urls", _no_pages),
    ):
        await scraper_loop(
            links_to_scrape=[DATA_URL],
            app="Example",
            app_version="1",
            on_documentation_scraped=on_documentation_scraped,
        )

    assert scheduled == []


@pytest.mark.asyncio
async def test_spec_referenced_by_a_page_is_saved_in_the_same_iteration() -> None:
    spec_url = "https://docs.example.test/specs/openapi.json"
    repository_spec_url = "https://github.com/example/api/blob/main/openapi.yaml"
    raw_spec_url = "https://raw.githubusercontent.com/example/api/main/openapi.yaml"
    foreign_spec_url = "https://other.test/openapi.json"
    page = _page(
        f'<html><body><redoc spec-url="{spec_url}"></redoc><main><p>{"Reference. " * 30}</p></main></body></html>',
        link_urls=(repository_spec_url, foreign_spec_url),
    )
    scheduled = []

    async def on_documentation_scraped(documentation) -> None:
        scheduled.append(documentation)

    async def content_types(urls: list[str]) -> dict[str, str]:
        return {url: "text/html" if url == PAGE_URL else "application/json" for url in urls}

    async def data_documentations(urls: list[str]) -> list[tuple[str, str]]:
        return [(url, OPENAPI_JSON) for url in urls]

    data_fetch = AsyncMock(side_effect=data_documentations)
    with (
        patch("src.modules.scrape.core.scraper.get_all_content_types", side_effect=content_types),
        patch("src.modules.scrape.core.scraper.scrape_all_data_documentations", data_fetch),
        patch("src.modules.scrape.core.scraper.scrape_urls", _scrape_urls_returning(page)),
        patch(
            "src.modules.scrape.core.scraper.filter_out_irrelevant_links",
            new_callable=AsyncMock,
            return_value=([], [spec_url]),
        ),
        patch(
            "src.modules.scrape.core.scraper.get_relevant_links_from_text",
            new_callable=AsyncMock,
            return_value=RelevantLinks(links=[spec_url]),
        ),
    ):
        new_links = await scraper_loop(
            links_to_scrape=[PAGE_URL],
            app="Example",
            app_version="1",
            trusted_domains=["example.test"],
            on_documentation_scraped=on_documentation_scraped,
            starter_links=[PAGE_URL],
        )

    assert [(documentation.url, documentation.retrieval.starter) for documentation in scheduled] == [
        (PAGE_URL, True),
        (spec_url, False),
        (raw_spec_url, False),
    ]
    assert scheduled[1].retrieval.spec.format == "openapi"
    # Raw GitHub files are fetched even when GitHub is not trusted; other foreign hosts are not.
    assert data_fetch.await_args_list[-1].args == ([spec_url, raw_spec_url],)
    # Already saved as a referenced spec, so it is not crawled again in the next iteration.
    assert new_links == []
