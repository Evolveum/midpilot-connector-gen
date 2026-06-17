# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
from typing import AsyncIterator, List, cast

import aiohttp
from crawl4ai import (  # type: ignore
    AsyncWebCrawler,
    CrawlResult,
    DefaultMarkdownGenerator,
    PruningContentFilter,
)
from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig  # type: ignore

from src.config import config

logger = logging.getLogger(__name__)


async def scrape_urls(links_to_scrape_orig: list[str]) -> AsyncIterator[CrawlResult]:
    """
    Scrape URLs and return successful CrawlResult objects.
    Retries failed URLs up to `max_attempts` times.
    """
    logger.info("[Scrape:URLs] Starting to scrape %s URLs", len(links_to_scrape_orig))
    prune_filter = PruningContentFilter(threshold=0.42, threshold_type="dynamic", min_word_threshold=1)
    md_generator = DefaultMarkdownGenerator(
        content_filter=prune_filter, options={"ignore_images": True, "skip_internal_links": True}
    )
    browser_config = BrowserConfig(
        verbose=config.scrape_and_process.crawl4ai_verbose
    )  # accept_downloads=True, browser_type="firefox"
    run_config = CrawlerRunConfig(
        # user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.131 Safari/537.36",
        # simulate_user= True,
        check_robots_txt=True,
        wait_until="networkidle",
        delay_before_return_html=1.5,
        stream=True,
        # screenshot=True,
        markdown_generator=md_generator,
        verbose=config.scrape_and_process.crawl4ai_verbose,
        log_console=False,
    )

    max_attempts = 3
    links_to_scrape = list(links_to_scrape_orig)
    scrape_out_success_count = 0
    new_failed_links: list[str] = []
    last_attempt = 0

    for attempt in range(1, max_attempts + 1):
        last_attempt = attempt
        logger.info("[Scrape:URLs] Attempt %s/%s: Scraping %s URLs", attempt, max_attempts, len(links_to_scrape))
        new_failed_links = []
        seen_links: set[str] = set()

        # create a fresh crawler each attempt and ensure clean shutdown
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
                    else:
                        if result_url:
                            new_failed_links.append(result_url.rstrip("/"))
            else:
                results: List[CrawlResult] = cast(List[CrawlResult], raw_results)
                for link, result in zip(links_to_scrape, results):
                    if getattr(result, "success", False):
                        scrape_out_success_count += 1
                        yield result
                    else:
                        new_failed_links.append(link.rstrip("/"))

        if seen_links:
            for link in links_to_scrape:
                normalized_link = link.rstrip("/")
                if normalized_link not in seen_links:
                    new_failed_links.append(normalized_link)

        new_failed_links = list(dict.fromkeys(new_failed_links))

        # If everything succeeded, we're done
        if not new_failed_links:
            logger.info("[Scrape:URLs] Attempt %s: All URLs scraped successfully", attempt)
            break

        # Otherwise, prepare for the next retry round
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

    logger.info(
        "[Scrape:URLs] Scraping complete: %s successful, %s failed",
        scrape_out_success_count,
        len(new_failed_links) if last_attempt == max_attempts else 0,
    )
    return


async def get_content_type(url: str) -> str:
    """
    Check content type without downloading the full documentation
    inputs:
        url: str - the URL to check
    outputs:
        str - the content type from the HTTP headers
    """
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",  # We need primarily HTML content for link extraction
        "Accept-Encoding": "identity",  # Disable compression for HEAD requests to avoid gzip parsing issues
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.131 Safari/537.36",
    }
    async with aiohttp.ClientSession() as session:
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
        except Exception as e:
            logger.error("[Scrape:ContentType] Failed to get content type for %s: %s", url, e)
            return ""


async def get_all_content_types(urls: list[str]) -> dict[str, str]:
    """
    Get content types for a list of URLs.
    inputs:
        urls: list - list of URLs to check
    outputs:
        dict - dictionary mapping URL to its content type
    """
    tasks = [get_content_type(url) for url in urls]
    content_types = await asyncio.gather(*tasks)
    return dict(zip(urls, content_types))


async def fetch_data_documentation(url: str) -> tuple[str, str] | None:
    """
    Fetch the content of a data documentation (e.g., JSON, YAML).
    inputs:
        url: str - the URL to fetch
    outputs:
        tuple[str, str] | None - the tuple of the url and the content of the documentation or None if failed
    """
    try:
        async with aiohttp.ClientSession() as http_session:
            async with http_session.get(url) as response:
                if response.status == 200:
                    content_type = response.headers.get("Content-Type", "")
                    # This has to be here because some great admins ignore RFC 7231 and return different content types for HEAD and GET requests...
                    if (
                        "json" in content_type
                        or "yaml" in content_type
                        or "yml" in content_type
                        or "text/plain" in content_type
                    ):
                        return (url, await response.text())
                    else:
                        logger.warning(
                            "[Scrape:DataDocumentation] URL %s has unsupported content type: %s, defaulting to crawl4ai scraping",
                            url,
                            content_type,
                        )
                        return (url, "error")
                else:
                    logger.error("[Scrape:DataDocumentation] Failed to fetch %s: HTTP %s", url, response.status)
                    return None
    except Exception as e:
        logger.error("[Scrape:DataDocumentation] Exception while fetching %s: %s", url, e)
        return None


async def scrape_all_data_documentations(links: list[str]) -> list[tuple[str, str]]:
    """
    Scrape all data documentations (e.g., JSON, YAML) from the provided links.
    inputs:
        links: list - list of URLs to scrape
    outputs:
        list - list of tuples mapping URL to its content
    """
    tasks = [fetch_data_documentation(link) for link in links]
    results = await asyncio.gather(*tasks)
    return [result for result in results if result is not None]
