# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Web-search apiType signals.

Given only the application name from discovery, these signals perform a web search (via the
shared search backend), optionally open the result pages to get their full content (via the
shared crawl4ai scraper), and feed that evidence into a single structured LLM call. This is
the deterministic "search-then-extract" pattern: our code controls the query and sees the
results, and the LLM only classifies the evidence. SCIM and REST share the search/fetch/format
pipeline and the page-fetch settings; only the query template, enable flag, prompt and output
schema are protocol-specific.

Each call is best-effort: when the feature is disabled, the name is empty, the search returns
nothing, or any step fails, a non-supporting result is returned (rather than raising) so
callers can safely fall back to the other signals.
"""

import asyncio
import logging
from typing import Dict, List, Optional, cast

from langchain_core.runnables.config import RunnableConfig

from src.config import config
from src.core.llm import build_structured_chain
from src.core.observability.langfuse import langfuse_handler
from src.integrations.web import SearchResult, fetch_markdown_pages, search_web
from src.modules.digester.extraction.llm_execution import invoke_llm
from src.modules.digester.prompts.apitype.web_search_prompts import (
    get_api_type_web_search_system_prompt,
    get_api_type_web_search_user_prompt,
    get_rest_web_search_system_prompt,
    get_rest_web_search_user_prompt,
)
from src.modules.digester.schemas import ApiTypeSignalResult, RestSignalResult

logger = logging.getLogger(__name__)

_LOG_PREFIX = "[ApiType:WebSearch] "
_REST_LOG_PREFIX = "[ApiType:RestWebSearch] "

# Cap each snippet so a few verbose results cannot blow up the prompt token budget.
_MAX_SNIPPET_CHARS = 500


def _normalize_url(url: str) -> str:
    return (url or "").rstrip("/")


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


async def _collect_search_evidence(application_name: str, query_template: str, log_prefix: str) -> Optional[str]:
    """
    Run the shared search-then-format pipeline for a protocol web-search signal.

    Returns the formatted search-results block ready for the prompt, or ``None`` when the
    search step fails or yields nothing (so the caller returns a non-supporting result).
    Uses the shared page-fetch settings for both protocols.
    """
    settings = config.digester
    try:
        query = query_template.format(application_name=application_name.strip())
        results = await asyncio.to_thread(search_web, query, max_results=settings.apitype_web_search_max_results)
    except Exception as exc:
        logger.warning("%sWeb search step failed for '%s', skipping signal: %s", log_prefix, application_name, exc)
        return None

    if not results:
        logger.info("%sNo web search results for '%s'; skipping signal", log_prefix, application_name)
        return None

    page_contents: Dict[str, str] = {}
    if settings.apitype_web_search_fetch_pages:
        urls = [result.href for result in results if result.href]
        if urls:
            page_contents = await fetch_markdown_pages(urls, logger_prefix=log_prefix, log=logger)
            logger.info(
                "%sFetched %d/%d result pages for '%s'", log_prefix, len(page_contents), len(urls), application_name
            )

    logger.info(
        "%sAnalyzing %d results (%d fetched pages) with LLM for '%s'",
        log_prefix,
        len(results),
        len(page_contents),
        application_name,
    )
    return _format_search_results(results, page_contents, settings.apitype_web_search_page_max_chars)


async def lookup_api_type_web_search(application_name: str) -> ApiTypeSignalResult:
    """
    Search the web for SCIM support/availability of ``application_name`` and classify it.

    Returns a non-supporting result (rather than raising) when the feature is disabled,
    the name is empty, the search yields nothing, or any step fails, so callers can
    safely fall back to the documentation-based and other documentation-free signals.
    """
    log_prefix = _LOG_PREFIX
    if not config.digester.apitype_scim_web_search_enabled:
        return ApiTypeSignalResult()
    if not application_name or not application_name.strip():
        logger.info("%sNo application name provided; skipping web search", log_prefix)
        return ApiTypeSignalResult()

    search_results = await _collect_search_evidence(
        application_name, config.digester.apitype_scim_web_search_query_template, log_prefix
    )
    if search_results is None:
        return ApiTypeSignalResult()

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
                config=RunnableConfig(callbacks=[langfuse_handler], run_name="Digester:ApiTypeWebSearch"),
            ),
        )
    except Exception as exc:
        logger.warning("%sWeb search LLM analysis failed for '%s': %s", log_prefix, application_name, exc)
        return ApiTypeSignalResult()

    if not result:
        logger.warning("%sEmpty web search analysis for '%s'", log_prefix, application_name)
        return ApiTypeSignalResult()

    logger.info(
        "%s'%s' web result: supports_scim=%s, api_types=%s, scim_availability=%s, required_plan=%s",
        log_prefix,
        application_name,
        result.supports_scim,
        [api_type.value for api_type in result.api_type],
        result.scim_availability.value,
        result.required_plan or "-",
    )
    return result


async def lookup_rest_web_search(application_name: str) -> RestSignalResult:
    """
    Search the web for REST/OpenAPI support/availability of ``application_name`` and classify it.

    Returns a non-supporting result (rather than raising) when the feature is disabled,
    the name is empty, the search yields nothing, or any step fails, so callers can
    safely fall back to the documentation-based and knowledge signals.
    """
    log_prefix = _REST_LOG_PREFIX
    if not config.digester.apitype_rest_web_search_enabled:
        return RestSignalResult()
    if not application_name or not application_name.strip():
        logger.info("%sNo application name provided; skipping web search", log_prefix)
        return RestSignalResult()

    search_results = await _collect_search_evidence(
        application_name, config.digester.apitype_rest_web_search_query_template, log_prefix
    )
    if search_results is None:
        return RestSignalResult()

    chain = build_structured_chain(
        get_rest_web_search_system_prompt,
        get_rest_web_search_user_prompt,
        RestSignalResult,
        user_role="human",
    )

    try:
        result = cast(
            RestSignalResult,
            await invoke_llm(
                chain,
                {"application_name": application_name, "search_results": search_results},
                config=RunnableConfig(callbacks=[langfuse_handler], run_name="Digester:RestWebSearch"),
            ),
        )
    except Exception as exc:
        logger.warning("%sWeb search LLM analysis failed for '%s': %s", log_prefix, application_name, exc)
        return RestSignalResult()

    if not result:
        logger.warning("%sEmpty web search analysis for '%s'", log_prefix, application_name)
        return RestSignalResult()

    logger.info(
        "%s'%s' web result: supports_rest=%s, availability=%s, required_plan=%s",
        log_prefix,
        application_name,
        result.supports_rest,
        result.availability.value,
        result.required_plan or "-",
    )
    return result
