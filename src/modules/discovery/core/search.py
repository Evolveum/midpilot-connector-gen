# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.


import logging
from typing import List

import requests
from ddgs import DDGS

from src.config import config

from ..schema import SearchResult

logger = logging.getLogger(__name__)

DEFAULT_MAX_RESULTS = 10
HTTP_TIMEOUT_SECS = 15


def normalize_result(*, title: str | None, href: str | None, body: str | None, source: str) -> SearchResult:
    return SearchResult(
        title=title or "",
        href=href or "",
        body=body or "",
        source=source,
    )


def search_with_ddgs(query: str, *, max_results: int = DEFAULT_MAX_RESULTS) -> List[SearchResult]:
    """Search via ddgs (DuckDuckGo) helper."""
    logger.info("Web search method: ddgs package")
    try:
        results: List[SearchResult] = []
        # DDGS supports multiple backends; keep close to original intent.
        with DDGS() as ddgs:
            for item in ddgs.text(
                query,
                max_results=max_results,
                backend=["bing", "brave", "yahoo"],
            ):
                results.append(
                    normalize_result(
                        title=item.get("title"),
                        href=item.get("href") or item.get("url"),
                        body=item.get("body") or item.get("description") or item.get("snippet"),
                        source="ddgs",
                    )
                )
        logger.info("Web search results count (ddgs): %d", len(results))
        return results
    except Exception:
        logger.exception("DDGS search failed")
        return []


def search_with_brave(query: str, *, max_results: int = DEFAULT_MAX_RESULTS) -> List[SearchResult]:
    """Search via Brave Search API."""
    logger.info("Web search method: Brave API")
    endpoint = config.brave.endpoint
    api_key = config.brave.api_key

    if not endpoint or not api_key:
        logger.warning("Brave API not configured (missing endpoint or api_key).")
        return []

    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": api_key,
    }
    params: list[tuple[str, str | bytes | int | float]] = [
        ("q", query),
        ("count", max_results),
        ("country", "us"),
        ("safesearch", "moderate"),
    ]

    try:
        with requests.Session() as session:
            resp = session.get(endpoint, headers=headers, params=params, timeout=HTTP_TIMEOUT_SECS)
            resp.raise_for_status()
            data = resp.json()
    except requests.RequestException:
        logger.exception("Brave web search request failed")
        return []
    except ValueError:
        logger.exception("Brave web search: JSON decode failed")
        return []

    results: List[SearchResult] = []
    for item in (data.get("web", {}) or {}).get("results", [])[:max_results]:
        results.append(
            normalize_result(
                title=item.get("title"),
                href=item.get("url") or item.get("link"),
                body=item.get("description") or item.get("snippet"),
                source="brave",
            )
        )

    logger.info("Web search results count (brave): %d", len(results))
    return results


def search_web(query: str, *, max_results: int = DEFAULT_MAX_RESULTS) -> List[SearchResult]:
    """
    Query web using configured backend and return normalized results.
    """
    logger.info("Executing web search with the following query: %s", query)

    method = (config.search.method_name or "").lower()
    if method == "ddgs":
        return search_with_ddgs(query, max_results=max_results)
    if method == "brave":
        return search_with_brave(query, max_results=max_results)

    logger.warning("No valid search method specified ('%s'); returning empty list.", method)
    return []
