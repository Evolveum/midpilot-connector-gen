# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import json
import time
from contextlib import ExitStack
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from src.config import config
from src.core.errors import LLMUnavailableError
from src.documents import PageAssessmentMetadata, SavedDocumentation
from src.integrations.web.page_links import page_links
from src.modules.scrape.core.candidates import CrawlScope
from src.modules.scrape.core.scraper import ScrapeRun, scraper_loop

SCRAPER = "src.modules.scrape.core.scraper"
PAGE_URL = "https://docs.example.test/api"
USERS_URL = "https://docs.example.test/api/users"
PAGE_HTML = f"<html><body><main><h1>Users API</h1><p>{'Lists users. ' * 30}</p></main></body></html>"


def _last_call(mock: AsyncMock) -> Any:
    assert mock.await_args is not None
    return mock.await_args


def _retrieval(documentation: SavedDocumentation) -> Any:
    assert documentation.retrieval is not None
    return documentation.retrieval


def _page(html: str = PAGE_HTML, *, url: str = PAGE_URL, link_urls: tuple[str, ...] = (USERS_URL,)) -> SimpleNamespace:
    return SimpleNamespace(
        url=url,
        html=html,
        links={"internal": [{"href": link_url, "text": link_url.rsplit("/", 1)[-1]} for link_url in link_urls]},
        metadata={"title": "Users API"},
    )


def _scrape_urls_returning(*pages: SimpleNamespace):
    async def _scrape(_links: list[str], **_options):
        for page in pages:
            yield page

    return _scrape


def _assessment(**overrides: Any) -> PageAssessmentMetadata:
    return PageAssessmentMetadata.model_validate(
        {"role": "api_reference", "product_match": "yes", "covers": ["users"], "object_classes": ["User"], **overrides}
    )


def _assess(keep: bool = True, **overrides: Any) -> AsyncMock:
    return AsyncMock(return_value=(keep, _assessment(**overrides)))


def _select_all() -> AsyncMock:
    async def select(links, **_kwargs):
        return list(links)

    return AsyncMock(side_effect=select)


class _Recorder:
    def __init__(self) -> None:
        self.saved: list[SavedDocumentation] = []
        self.errors: list[str] = []

    async def on_documentation_scraped(self, documentation: SavedDocumentation) -> None:
        self.saved.append(documentation)

    async def report_error(self, message: str) -> None:
        self.errors.append(message)

    @property
    def saved_urls(self) -> list[str]:
        return [documentation.url for documentation in self.saved]


def _run(recorder: _Recorder, *, starters: tuple[str, ...] = (PAGE_URL,), **overrides: Any) -> ScrapeRun:
    return ScrapeRun(
        app="Example",
        version=overrides.pop("version", None),
        scope=CrawlScope.from_starter_links(starters),
        starter_links=overrides.pop("starter_links", starters),
        on_documentation_scraped=recorder.on_documentation_scraped,
        report_error=recorder.report_error,
        **overrides,
    )


def _patches(
    stack: ExitStack,
    *pages: SimpleNamespace,
    content_types: dict[str, str] | None = None,
    data_files: list[tuple[str, str]] | None = None,
    assess: AsyncMock | None = None,
    select: AsyncMock | None = None,
) -> tuple[AsyncMock, AsyncMock]:
    assess = assess or _assess()
    select = select or _select_all()
    types = content_types or {page.url: "text/html" for page in pages}
    stack.enter_context(patch(f"{SCRAPER}.get_all_content_types", AsyncMock(return_value=types)))
    stack.enter_context(patch(f"{SCRAPER}.scrape_all_data_documentations", AsyncMock(return_value=data_files or [])))
    stack.enter_context(patch(f"{SCRAPER}.scrape_urls", _scrape_urls_returning(*pages)))
    stack.enter_context(patch(f"{SCRAPER}.assess_page", assess))
    stack.enter_context(patch(f"{SCRAPER}.select_links", select))
    return assess, select


def _slow_page_links(links, page_url):
    time.sleep(0.3)
    return page_links(links, page_url)


@pytest.mark.asyncio
async def test_link_extraction_does_not_block_the_event_loop() -> None:
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
    with ExitStack() as stack:
        _patches(stack, _page())
        stack.enter_context(patch(f"{SCRAPER}.page_links", _slow_page_links))
        try:
            new_links = await scraper_loop([PAGE_URL], _run(_Recorder()))
        finally:
            scraping_done.set()
            await ticker

    assert new_links == [USERS_URL]
    assert largest_gap < 0.15


@pytest.mark.asyncio
async def test_kept_page_is_saved_with_its_assessment_and_its_links_are_selected() -> None:
    recorder = _Recorder()
    out_of_scope = "https://other.test/users"
    forbidden = "https://docs.example.test/blog"
    with ExitStack() as stack:
        assess, select = _patches(stack, _page(link_urls=(USERS_URL, f"{USERS_URL}/#list", out_of_scope, forbidden)))
        new_links = await scraper_loop(
            [PAGE_URL], _run(recorder, version="2.0", forbidden_url_parts=["blog"]), curr_iteration=2
        )

    assert recorder.saved_urls == [PAGE_URL]
    retrieval = _retrieval(recorder.saved[0])
    assert (retrieval.starter, retrieval.depth, retrieval.assessment) == (True, 1, _assessment())
    assert _last_call(assess).kwargs | {"content": ""} == {
        "url": PAGE_URL,
        "title": "Users API",
        "content": "",
        "app": "Example",
        "version": "2.0",
    }
    # Only the in-scope link that is not forbidden is offered, once, without its fragment.
    assert [link.url for link in _last_call(select).args[0]] == [USERS_URL]
    assert new_links == [USERS_URL]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("keep", "overrides", "follows_links"),
    [
        (False, {"role": "navigation"}, True),
        (True, {"product_match": "no", "product_evidence": "Other Product API"}, False),
    ],
)
async def test_page_that_is_no_documentation_or_of_another_product_is_not_saved(keep, overrides, follows_links) -> None:
    recorder = _Recorder()
    page_url = "https://docs.example.test/api/overview"
    with ExitStack() as stack:
        _patches(stack, _page(url=page_url), assess=_assess(keep, **overrides))
        new_links = await scraper_loop([page_url], _run(recorder))

    assert recorder.saved == [] and recorder.errors == []
    assert new_links == ([USERS_URL] if follows_links else [])


@pytest.mark.asyncio
async def test_starter_link_of_another_product_is_saved_with_a_job_error() -> None:
    recorder = _Recorder()
    with ExitStack() as stack:
        _patches(stack, _page(), assess=_assess(False, product_match="no", product_evidence="Other Product API"))
        new_links = await scraper_loop([PAGE_URL], _run(recorder))

    assert recorder.saved_urls == [PAGE_URL]
    assert _retrieval(recorder.saved[0]).assessment.product_match == "no"
    assert recorder.errors == [
        f"Starter link {PAGE_URL} documents another product ('Other Product API'); it is saved because it is a "
        "starter link, and its links are not followed."
    ]
    assert new_links == []


@pytest.mark.asyncio
@pytest.mark.parametrize("starter", [False, True])
async def test_page_of_another_version_is_saved_and_its_links_are_followed(starter: bool) -> None:
    """Vendors often publish only their latest documentation (Nextcloud: /server/stable/), the best there is."""
    recorder = _Recorder()
    page_url = PAGE_URL if starter else "https://docs.example.test/api/overview"
    with ExitStack() as stack:
        _patches(stack, _page(url=page_url), assess=_assess(True, version_match="no", version_evidence="Version 35"))
        new_links = await scraper_loop([page_url], _run(recorder, version="31"))

    assert recorder.saved_urls == [page_url]
    assert _retrieval(recorder.saved[0]).assessment.version_match == "no"
    assert new_links == [USERS_URL]
    assert recorder.errors == (
        [
            f"Starter link {PAGE_URL} documents another version than 31 ('Version 35'); it is saved and its links are followed."
        ]
        if starter
        else []
    )


@pytest.mark.asyncio
async def test_page_whose_assessment_fails_is_saved_unassessed_with_a_job_error() -> None:
    recorder = _Recorder()
    page_url = "https://docs.example.test/api/overview"
    with ExitStack() as stack:
        _patches(stack, _page(url=page_url), assess=AsyncMock(side_effect=ValueError("unparseable output")))
        new_links = await scraper_loop([page_url], _run(recorder))

    assert recorder.saved_urls == [page_url]
    assert _retrieval(recorder.saved[0]).assessment is None
    assert recorder.errors == [f"Page assessment failed for {page_url}; saved without assessment: unparseable output"]
    assert new_links == [USERS_URL]


@pytest.mark.asyncio
async def test_llm_outage_during_page_assessment_fails_the_iteration() -> None:
    with ExitStack() as stack:
        _patches(stack, _page(), assess=AsyncMock(side_effect=LLMUnavailableError("page assessment")))
        with pytest.raises(LLMUnavailableError):
            await scraper_loop([PAGE_URL], _run(_Recorder()))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "html",
    ["", "<html><body>  </body></html>", "<html><body><nav><a href='/users'>Users</a></nav></body></html>"],
)
async def test_page_without_main_content_is_not_saved_but_its_links_are_followed(html: str) -> None:
    recorder = _Recorder()
    with ExitStack() as stack:
        assess, _ = _patches(stack, _page(html))
        new_links = await scraper_loop([PAGE_URL], _run(recorder))

    assess.assert_not_awaited()
    assert recorder.saved == []
    assert new_links == [USERS_URL]


@pytest.mark.asyncio
async def test_last_iteration_reports_remaining_links_without_llm_selection() -> None:
    recorder = _Recorder()
    with ExitStack() as stack:
        _, select = _patches(stack, _page())
        new_links = await scraper_loop([PAGE_URL], _run(recorder), last_iteration=True)

    select.assert_not_awaited()
    assert recorder.saved_urls == [PAGE_URL]
    assert new_links == [USERS_URL]


@pytest.mark.asyncio
async def test_links_are_selected_in_batches_and_a_failed_batch_is_reported(monkeypatch) -> None:
    monkeypatch.setattr(config.scrape_and_process, "max_links_per_assessment", 2)
    recorder = _Recorder()
    link_urls = tuple(f"https://docs.example.test/api/resource{index}" for index in range(5))

    async def select(links, **_kwargs):
        if links[0].url == link_urls[2]:
            raise ValueError("unparseable output")
        return [links[0]]

    with ExitStack() as stack:
        _, selection = _patches(stack, _page(link_urls=link_urls), select=AsyncMock(side_effect=select))
        run = _run(recorder)
        new_links = await scraper_loop([PAGE_URL], run)

    assert [len(call.args[0]) for call in selection.await_args_list] == [2, 2, 1]
    assert new_links == [link_urls[0], link_urls[4]]
    assert recorder.errors == ["Link selection failed for 2 links found on the scraped pages: unparseable output"]
    # Every offered link is decided: none of them is offered again in a later iteration.
    assert set(link_urls) <= run.seen_links


OPENAPI_JSON = '{"openapi": "3.0.3", "info": {"title": "Example", "version": "1"}, "paths": {"/users": {}}}'
PACKAGE_JSON = '{"name": "docs-portal", "dependencies": {"react": "^18"}}'
DATA_URL = "https://docs.example.test/files/data.json"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "starter", "spec_format", "assessed"),
    [
        (OPENAPI_JSON, False, "openapi", False),
        (PACKAGE_JSON, True, None, False),
        ("# Users\n\nRaw markdown documentation of the users endpoint.", False, None, True),
    ],
)
async def test_data_files_that_are_documentation_are_saved(content: str, starter: bool, spec_format, assessed) -> None:
    recorder = _Recorder()
    with ExitStack() as stack:
        assess, _ = _patches(stack, content_types={DATA_URL: "application/json"}, data_files=[(DATA_URL, content)])
        await scraper_loop([DATA_URL], _run(recorder, starters=(PAGE_URL, DATA_URL) if starter else (PAGE_URL,)))

    assert recorder.saved_urls == [DATA_URL]
    retrieval = _retrieval(recorder.saved[0])
    assert retrieval.starter is starter
    assert (retrieval.spec.format if retrieval.spec else None) == spec_format
    assert assess.await_count == int(assessed)


@pytest.mark.asyncio
async def test_crawled_json_that_is_no_api_description_is_not_saved() -> None:
    recorder = _Recorder()
    with ExitStack() as stack:
        _patches(stack, content_types={DATA_URL: "application/json"}, data_files=[(DATA_URL, PACKAGE_JSON)])
        await scraper_loop([DATA_URL], _run(recorder))

    assert recorder.saved == []


@pytest.mark.asyncio
async def test_spec_referenced_by_a_page_is_saved_in_the_same_iteration() -> None:
    spec_url = "https://docs.example.test/specs/openapi.json"
    repository_spec_url = "https://github.com/example/api/blob/main/openapi.yaml"
    raw_spec_url = "https://raw.githubusercontent.com/example/api/main/openapi.yaml"
    foreign_spec_url = "https://other.test/openapi.json"
    page = _page(
        f'<html><body><redoc spec-url="{spec_url}"></redoc><main><p>{"Reference. " * 30}</p></main></body></html>',
        link_urls=(repository_spec_url, foreign_spec_url, spec_url),
    )
    recorder = _Recorder()

    async def content_types(urls: list[str]) -> dict[str, str]:
        return {url: "text/html" if url == PAGE_URL else "application/json" for url in urls}

    async def data_documentations(urls: list[str]) -> list[tuple[str, str]]:
        return [(url, OPENAPI_JSON.replace('"Example"', json.dumps(url))) for url in urls]

    data_fetch = AsyncMock(side_effect=data_documentations)
    with ExitStack() as stack:
        _patches(stack, page)
        stack.enter_context(patch(f"{SCRAPER}.get_all_content_types", side_effect=content_types))
        stack.enter_context(patch(f"{SCRAPER}.scrape_all_data_documentations", data_fetch))
        new_links = await scraper_loop([PAGE_URL], _run(recorder))

    assert [(documentation.url, _retrieval(documentation).starter) for documentation in recorder.saved] == [
        (PAGE_URL, True),
        (spec_url, False),
        (raw_spec_url, False),
    ]
    assert _retrieval(recorder.saved[1]).spec.format == "openapi"
    assert _retrieval(recorder.saved[1]).depth == 1
    # Raw GitHub files are fetched even though GitHub is not in scope; other foreign hosts are not.
    assert data_fetch.await_args_list[-1].args == ([spec_url, raw_spec_url],)
    # Already saved as a referenced spec, so it is not crawled again in the next iteration.
    assert new_links == []


@pytest.mark.asyncio
async def test_page_of_another_product_leads_to_no_referenced_spec() -> None:
    spec_url = "https://docs.example.test/specs/openapi.json"
    page = _page(f'<html><body><redoc spec-url="{spec_url}"></redoc>{PAGE_HTML}</body></html>', link_urls=())
    recorder = _Recorder()
    with ExitStack() as stack:
        _patches(stack, page, assess=_assess(True, product_match="no", product_evidence="Other Product"))
        data_fetch = stack.enter_context(patch(f"{SCRAPER}.scrape_all_data_documentations", AsyncMock(return_value=[])))
        await scraper_loop([PAGE_URL], _run(recorder, starters=("https://docs.example.test/start",)))

    assert recorder.saved == []
    assert [call.args for call in data_fetch.await_args_list] == [([],)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("starter", "saved"), [(False, [DATA_URL]), (True, [DATA_URL, "https://docs.example.test/openapi.yaml"])]
)
async def test_second_copy_of_a_saved_api_description_is_saved_only_as_a_starter(
    starter: bool, saved: list[str]
) -> None:
    yaml_url = "https://docs.example.test/openapi.yaml"
    instance_copy = yaml.safe_dump({**json.loads(OPENAPI_JSON), "servers": [{"url": "https://tenant.example.test"}]})
    recorder = _Recorder()
    with ExitStack() as stack:
        _patches(
            stack,
            content_types={DATA_URL: "application/json", yaml_url: "application/yaml"},
            data_files=[(DATA_URL, OPENAPI_JSON), (yaml_url, instance_copy)],
        )
        await scraper_loop(
            [DATA_URL, yaml_url], _run(recorder, starters=(PAGE_URL, yaml_url) if starter else (PAGE_URL,))
        )

    assert recorder.saved_urls == saved
