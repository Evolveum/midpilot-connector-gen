# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Web-search apiType signal.

Given only the application name from discovery, this signal performs a web search
(via the shared search backend, e.g. Brave) for SCIM support/availability, optionally
opens the result pages to get their full content (via the shared crawl4ai scraper),
and feeds that evidence into a single structured LLM call. This is the deterministic
"search-then-extract" pattern: our code controls the query and sees the results, and
the LLM only classifies the evidence (the self-hosted model cannot browse on its own).

The call is best-effort: when the feature is disabled, the name is empty, the search
returns nothing, or any step fails, a non-supporting result is returned (rather than
raising) so callers can safely fall back to the other signals.
"""

import asyncio
import logging
from typing import Dict, List, cast

from langchain_core.runnables.config import RunnableConfig

from src.common.langfuse import langfuse_handler
from src.common.llm import build_structured_chain
from src.config import config
from src.modules.digester.extraction.llm_execution import invoke_llm
from src.modules.digester.prompts.apitype.web_search_prompts import (
    get_api_type_web_search_system_prompt,
    get_api_type_web_search_user_prompt,
)
from src.modules.digester.schemas import ApiTypeSignalResult

# TODO
# move to shared folder
# Reuse the shared web search backend instead of duplicating the Brave/ddgs client.
from src.modules.discovery.core.search import search_web
from src.modules.discovery.schema import SearchResult
from src.modules.scrape.core.crawling import scrape_urls

logger = logging.getLogger(__name__)

_LOG_PREFIX = "[ApiType:WebSearch] "

# Cap each snippet so a few verbose results cannot blow up the prompt token budget.
_MAX_SNIPPET_CHARS = 500


def _normalize_url(url: str) -> str:
    return (url or "").rstrip("/")


async def _fetch_page_contents(urls: List[str]) -> Dict[str, str]:
    """
    Open the given URLs and return a map of normalized URL -> fetched page markdown.

    Reuses the shared crawl4ai scraper. Best-effort: any failure (or a page that cannot
    be scraped) is simply omitted so the caller falls back to that result's snippet.
    """

    contents: Dict[str, str] = {}
    try:
        async for result in scrape_urls(urls):
            url = _normalize_url(str(getattr(result, "url", "") or ""))
            markdown = getattr(result, "markdown", None)
            text = getattr(markdown, "fit_markdown", None) if markdown is not None else None
            if url and text:
                contents[url] = text
    except Exception as exc:
        logger.warning("%sPage fetch failed, falling back to snippets: %s", _LOG_PREFIX, exc)
    return contents


def _format_search_results(
    results: List[SearchResult],
    page_contents: Dict[str, str],
    page_max_chars: int,
) -> str:
    """
    Render results as numbered blocks for the prompt.

    When a result's page was fetched, its full content is used (truncated to
    ``page_max_chars``); otherwise the search snippet is used as a fallback.
    """
    blocks: List[str] = []
    for index, result in enumerate(results, start=1):
        full_content = page_contents.get(_normalize_url(result.href))
        if full_content:
            body = full_content.strip()
            if len(body) > page_max_chars:
                body = body[:page_max_chars] + "…"
            label = "PAGE CONTENT"
        else:
            body = (result.body or "").strip()
            if len(body) > _MAX_SNIPPET_CHARS:
                body = body[:_MAX_SNIPPET_CHARS] + "…"
            label = "SNIPPET"
        blocks.append(f"[{index}] {result.title}\nURL: {result.href}\n{label}:\n{body}")
    return "\n\n".join(blocks)


async def lookup_api_type_web_search(application_name: str) -> ApiTypeSignalResult:
    """
    Search the web for SCIM support/availability of ``application_name`` and classify it.

    Returns a non-supporting result (rather than raising) when the feature is disabled,
    the name is empty, the search yields nothing, or any step fails, so callers can
    safely fall back to the documentation-based and other documentation-free signals.
    """
    settings = config.digester
    if not settings.apitype_web_search_enabled:
        return ApiTypeSignalResult()
    if not application_name or not application_name.strip():
        logger.info("%sNo application name provided; skipping web search", _LOG_PREFIX)
        return ApiTypeSignalResult()

    try:
        query = settings.apitype_web_search_query_template.format(application_name=application_name.strip())
        results = await asyncio.to_thread(search_web, query, max_results=settings.apitype_web_search_max_results)
    except Exception as exc:
        logger.warning("%sWeb search step failed for '%s', skipping signal: %s", _LOG_PREFIX, application_name, exc)
        return ApiTypeSignalResult()

    if not results:
        logger.info("%sNo web search results for '%s'; skipping signal", _LOG_PREFIX, application_name)
        return ApiTypeSignalResult()

    page_contents: Dict[str, str] = {}
    if settings.apitype_web_search_fetch_pages:
        urls = [result.href for result in results if result.href]
        if urls:
            page_contents = await _fetch_page_contents(urls)
            logger.info(
                "%sFetched %d/%d result pages for '%s'", _LOG_PREFIX, len(page_contents), len(urls), application_name
            )

    search_results = _format_search_results(results, page_contents, settings.apitype_web_search_page_max_chars)

    logger.info(
        "%sAnalyzing %d results (%d fetched pages) with LLM for '%s'",
        _LOG_PREFIX,
        len(results),
        len(page_contents),
        application_name,
    )

    chain = build_structured_chain(
        get_api_type_web_search_system_prompt,
        get_api_type_web_search_user_prompt,
        ApiTypeSignalResult,
        user_role="human",
    )

    try:
        result = cast(
            ApiTypeSignalResult,
            await invoke_llm(
                chain,
                {"application_name": application_name, "search_results": search_results},
                config=RunnableConfig(callbacks=[langfuse_handler]),
            ),
        )
    except Exception as exc:
        logger.warning("%sWeb search LLM analysis failed for '%s': %s", _LOG_PREFIX, application_name, exc)
        return ApiTypeSignalResult()

    if not result:
        logger.warning("%sEmpty web search analysis for '%s'", _LOG_PREFIX, application_name)
        return ApiTypeSignalResult()

    logger.info(
        "%s'%s' web result: supports_scim=%s, api_types=%s, scim_availability=%s, required_plan=%s (results=%d)",
        _LOG_PREFIX,
        application_name,
        result.supports_scim,
        [api_type.value for api_type in result.api_type],
        result.scim_availability.value,
        result.required_plan or "-",
        len(results),
    )
    return result
