# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
from typing import AsyncIterator, Dict, List, cast

import aiohttp
from crawl4ai import (  # type: ignore
    AsyncWebCrawler,
    CrawlResult,
    DefaultMarkdownGenerator,
    PruningContentFilter,
)
from crawl4ai.async_configs import CrawlerRunConfig  # type: ignore

from src.config import config
from src.integrations.web.browser import build_browser_config

logger = logging.getLogger(__name__)


def _normalize_url(url: str) -> str:
    return (url or "").rstrip("/")


# 4xx responses that can succeed on a later attempt; every other 4xx (and a robots.txt denial) is final.
_RETRYABLE_CLIENT_ERROR_STATUSES = frozenset({408, 425, 429})


def _is_retryable_failure(result: CrawlResult) -> bool:
    """Return False for failures a retry cannot fix: a robots.txt denial or a permanent 4xx response."""
    headers = getattr(result, "response_headers", None) or {}
    if "X-Robots-Status" in headers or "robots.txt" in str(getattr(result, "error_message", "") or ""):
        return False
    status_code = getattr(result, "status_code", None)
    if isinstance(status_code, int) and 400 <= status_code < 500:
        return status_code in _RETRYABLE_CLIENT_ERROR_STATUSES
    return True


async def scrape_urls(links_to_scrape_orig: list[str], *, prune_content: bool = True) -> AsyncIterator[CrawlResult]:
    """
    Scrape URLs and return successful CrawlResult objects.
    Retries failed URLs up to `max_attempts` times.

    ``prune_content`` fills ``markdown.fit_markdown`` with crawl4ai's pruned page text. The scraper turns
    it off: it extracts the main content from ``html`` itself and needs the markdown only for links.
    """
    logger.info("[Scrape:URLs] Starting to scrape %s URLs", len(links_to_scrape_orig))
    prune_filter = (
        PruningContentFilter(threshold=0.42, threshold_type="dynamic", min_word_threshold=1) if prune_content else None
    )
    md_generator = DefaultMarkdownGenerator(
        content_filter=prune_filter, options={"ignore_images": True, "skip_internal_links": True}
    )
    browser_config = build_browser_config(verbose=config.scrape_and_process.crawl4ai_verbose)
    run_config = CrawlerRunConfig(
        check_robots_txt=True,
        wait_until="networkidle",
        delay_before_return_html=1.5,
        stream=True,
        markdown_generator=md_generator,
        verbose=config.scrape_and_process.crawl4ai_verbose,
        log_console=False,
    )

    max_attempts = 3
    links_to_scrape = list(links_to_scrape_orig)
    scrape_out_success_count = 0
    new_failed_links: list[str] = []
    permanently_failed_links: list[str] = []
    last_attempt = 0

    for attempt in range(1, max_attempts + 1):
        last_attempt = attempt
        logger.info("[Scrape:URLs] Attempt %s/%s: Scraping %s URLs", attempt, max_attempts, len(links_to_scrape))
        new_failed_links = []
        seen_links: set[str] = set()

        async with AsyncWebCrawler(config=browser_config) as crawler:
            raw_results = await crawler.arun_many(urls=links_to_scrape, config=run_config)

            if hasattr(raw_results, "__aiter__"):
                async for result in cast(AsyncIterator[CrawlResult], raw_results):
                    result_url = str(getattr(result, "url", "") or "")
                    if result_url:
                        seen_links.add(result_url.rstrip("/"))

                    if getattr(result, "success", False):
                        scrape_out_success_count += 1
                        yield result
                    elif result_url and _is_retryable_failure(result):
                        new_failed_links.append(result_url.rstrip("/"))
                    elif result_url:
                        permanently_failed_links.append(result_url.rstrip("/"))
            else:
                results: List[CrawlResult] = cast(List[CrawlResult], raw_results)
                for link, result in zip(links_to_scrape, results):
                    if getattr(result, "success", False):
                        scrape_out_success_count += 1
                        yield result
                    elif _is_retryable_failure(result):
                        new_failed_links.append(link.rstrip("/"))
                    else:
                        permanently_failed_links.append(link.rstrip("/"))

        if seen_links:
            for link in links_to_scrape:
                normalized_link = link.rstrip("/")
                if normalized_link not in seen_links:
                    new_failed_links.append(normalized_link)

        new_failed_links = list(dict.fromkeys(new_failed_links))

        if not new_failed_links:
            logger.info("[Scrape:URLs] Attempt %s: No URLs left to retry", attempt)
            break

        logger.warning(
            "[Scrape:URLs] Attempt %s failed for %s/%s URLs",
            attempt,
            len(new_failed_links),
            len(links_to_scrape),
        )
        logger.debug("[Scrape:URLs] Failed URLs: %s", new_failed_links)

        if attempt == max_attempts:
            logger.error(
                "[Scrape:URLs] All scraping attempts exhausted. %s URLs failed permanently", len(new_failed_links)
            )
            break

        links_to_scrape = new_failed_links

    permanently_failed_links = list(dict.fromkeys(permanently_failed_links))
    if permanently_failed_links:
        logger.warning(
            "[Scrape:URLs] %s URLs were not retried (robots.txt denial or permanent 4xx response): %s",
            len(permanently_failed_links),
            permanently_failed_links,
        )
    logger.info(
        "[Scrape:URLs] Scraping complete: %s successful, %s failed",
        scrape_out_success_count,
        (len(new_failed_links) if last_attempt == max_attempts else 0) + len(permanently_failed_links),
    )
    return


async def fetch_markdown_pages(
    urls: list[str],
    *,
    logger_prefix: str = "",
    log: logging.Logger | None = None,
) -> Dict[str, str]:
    """
    Open URLs and return a normalized URL -> fetched page markdown map.

    Best-effort: failed pages are omitted so callers can fall back to snippets or
    other evidence sources.
    """
    active_logger = log or logger
    contents: Dict[str, str] = {}
    try:
        async for result in scrape_urls(urls):
            url = _normalize_url(str(getattr(result, "url", "") or ""))
            markdown = getattr(result, "markdown", None)
            text = getattr(markdown, "fit_markdown", None) if markdown is not None else None
            if url and text:
                contents[url] = text
    except Exception as exc:
        active_logger.warning("%sPage fetch failed, falling back to snippets: %s", logger_prefix, exc)
    return contents


async def get_content_type(url: str) -> str:
    """
    Check content type without downloading the full documentation.
    """
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Encoding": "identity",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/92.0.4515.131 Safari/537.36"
        ),
    }
    async with aiohttp.ClientSession(trust_env=True) as session:
        try:
            response = await session.head(
                url, headers=headers, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=10)
            )
            logger.debug(
                "[Scrape:ContentType] Checked content type for %s: %s",
                url,
                response.headers.get("Content-Type", "unknown"),
            )
            return response.headers.get("Content-Type", "")
        except Exception as exc:
            logger.error("[Scrape:ContentType] Failed to get content type for %s: %s", url, exc)
            return ""


async def get_all_content_types(urls: list[str]) -> dict[str, str]:
    """
    Get content types for a list of URLs.
    """
    tasks = [get_content_type(url) for url in urls]
    content_types = await asyncio.gather(*tasks)
    return dict(zip(urls, content_types))


async def read_text_limited(response: aiohttp.ClientResponse, max_bytes: int) -> str | None:
    """Read a response body as text, or return None when it is larger than ``max_bytes``."""
    if response.content_length is not None and response.content_length > max_bytes:
        return None
    body = bytearray()
    async for block in response.content.iter_chunked(64 * 1024):
        body.extend(block)
        if len(body) > max_bytes:
            return None
    try:
        return body.decode(response.charset or "utf-8", errors="replace")
    except LookupError:  # an unknown charset name in Content-Type
        return body.decode("utf-8", errors="replace")


async def fetch_data_documentation(url: str) -> tuple[str, str] | None:
    """
    Fetch the content of a data documentation (e.g., JSON, YAML).
    """
    try:
        async with aiohttp.ClientSession(trust_env=True) as http_session:
            async with http_session.get(url) as response:
                if response.status == 200:
                    content_type = response.headers.get("Content-Type", "")
                    # Some servers return different content types for HEAD and GET requests.
                    if (
                        "json" in content_type
                        or "yaml" in content_type
                        or "yml" in content_type
                        or "text/plain" in content_type
                    ):
                        max_bytes = config.scrape_and_process.max_data_documentation_bytes
                        text = await read_text_limited(response, max_bytes)
                        if text is None:
                            logger.warning(
                                "[Scrape:DataDocumentation] Skipping %s: larger than %s bytes "
                                "(SCRAPE_AND_PROCESS__MAX_DATA_DOCUMENTATION_BYTES)",
                                url,
                                max_bytes,
                            )
                            return None
                        return (url, text)

                    logger.warning(
                        "[Scrape:DataDocumentation] URL %s has unsupported content type: %s, defaulting to crawl4ai scraping",
                        url,
                        content_type,
                    )
                    return (url, "error")

                logger.error("[Scrape:DataDocumentation] Failed to fetch %s: HTTP %s", url, response.status)
                return None
    except Exception as exc:
        logger.error("[Scrape:DataDocumentation] Exception while fetching %s: %s", url, exc)
        return None


async def scrape_all_data_documentations(links: list[str]) -> list[tuple[str, str]]:
    """
    Fetch all data documentations (e.g., JSON, YAML) from the provided links.
    """
    tasks = [fetch_data_documentation(link) for link in links]
    results = await asyncio.gather(*tasks)
    return [result for result in results if result is not None]
