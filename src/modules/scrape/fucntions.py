# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
from typing import List, cast
from urllib.parse import urljoin, urlparse

import aiohttp
from crawl4ai import (  # type: ignore
    AsyncWebCrawler,
    CrawlResult,
    DefaultMarkdownGenerator,
    PruningContentFilter,
)
from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig  # type: ignore
from crawl4ai.utils import (  # type: ignore
    get_base_domain,
    normalize_url,
)
from lxml import html as lhtml  # type: ignore
from pydantic import HttpUrl

from ...common.chunk_processor.schema import SavedPage
from ...common.schema import validate_pydantic_object
from .llms import get_irrelevant_llm_response
from .prompts import get_irrelevant_filter_prompts

logger = logging.getLogger(__name__)


async def scrape_urls(links_to_scrape_orig: list[str]) -> list[CrawlResult]:
    """
    Scrape URLs and return successful CrawlResult objects.
    Retries failed URLs up to `max_attempts` times.
    """
    logger.info("[Scrape:URLs] Starting to scrape %s URLs", len(links_to_scrape_orig))
    prune_filter = PruningContentFilter(threshold=0.42, threshold_type="dynamic", min_word_threshold=1)
    md_generator = DefaultMarkdownGenerator(content_filter=prune_filter)
    browser_config = BrowserConfig(accept_downloads=True, browser_type="firefox")
    run_config = CrawlerRunConfig(
        check_robots_txt=True,
        wait_until="networkidle",
        markdown_generator=md_generator,
    )

    max_attempts = 3
    links_to_scrape = list(links_to_scrape_orig)
    scrape_out_success: List[CrawlResult] = []
    # Was possibly unbound before
    new_failed_links: list[str] = []
    new_failed_results: List[CrawlResult] = []
    last_attempt = 0

    for attempt in range(1, max_attempts + 1):
        last_attempt = attempt
        logger.info("[Scrape:URLs] Attempt %s/%s: Scraping %s URLs", attempt, max_attempts, len(links_to_scrape))
        # create a fresh crawler each attempt and ensure clean shutdown
        async with AsyncWebCrawler(config=browser_config) as crawler:
            raw_results = await crawler.arun_many(urls=links_to_scrape, config=run_config)

        # Tell the type checker exactly what arun_many returns
        results: List[CrawlResult] = cast(List[CrawlResult], raw_results)

        new_failed_links = []
        new_failed_results = []

        # Pair each input URL with its corresponding result
        for link, result in zip(links_to_scrape, results):
            if getattr(result, "success", False):
                scrape_out_success.append(result)
            else:
                new_failed_links.append(link)
                new_failed_results.append(result)

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
        len(scrape_out_success),
        len(new_failed_links) if last_attempt == max_attempts else 0,
    )
    return scrape_out_success


async def get_content_type(url: str) -> str:
    """
    Check content type without downloading the full page
    inputs:
        url: str - the URL to check
    outputs:
        str - the content type from the HTTP headers
    """
    async with aiohttp.ClientSession() as session:
        async with session.head(url, allow_redirects=True) as response:
            return response.headers.get("Content-Type", "")


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


async def fetch_data_page(url: str) -> tuple[str, str] | None:
    """
    Fetch the content of a data page (e.g., JSON, YAML).
    inputs:
        url: str - the URL to fetch
    outputs:
        str | None - the content of the page or None if failed
    """
    try:
        async with aiohttp.ClientSession() as http_session:
            async with http_session.get(url) as response:
                if response.status == 200:
                    return (url, await response.text())
                else:
                    logger.error("[Scrape:DataPage] Failed to fetch %s: HTTP %s", url, response.status)
                    return None
    except Exception as e:
        logger.error("[Scrape:DataPage] Exception while fetching %s: %s", url, e)
        return None


async def scrape_all_data_pages(links: list[str]) -> list[tuple[str, str]]:
    """
    Scrape all data pages (e.g., JSON, YAML) from the provided links.
    inputs:
        links: list - list of URLs to scrape
    outputs:
        dict - dictionary mapping URL to its content
    """
    tasks = [fetch_data_page(link) for link in links]
    results = await asyncio.gather(*tasks)
    return [result for result in results if result is not None]


async def scraper_loop(
    links_to_scrape: list[str],
    app: str,
    app_version: str,
    max_iterations_filter_irrelevant: int = 5,
    max_scraper_iterations: int = 3,
    curr_iteration: int = 1,
    irrelevant_links: list[str] | None = None,
    saved_pages: dict[str, SavedPage] | None = None,
    trusted_domains: list[str] | None = None,
    forbidden_url_parts: list | None = None,
    suffixes: tuple = (".yml", ".yaml", ".json"),
):
    """
    Main scraper loop to scrape links, filter irrelevant ones, and process html content.
    inputs:
        links_to_scrape: list - list of links to scrape
        app: str - application name
        app_version: str - application version
        max_iterations_filter_irrelevant: int - maximum iterations for filtering irrelevant links
        max_scraper_iterations: int - maximum scraper iterations
        curr_iteration: int - current iteration count
        irrelevant_links: list - list of already identified irrelevant links
        saved_pages: dict - dictionary of already saved pages
        trusted_domains: list - list of trusted domains
        forbidden_url_parts: list - list of URL parts to filter out
    outputs:
        new_links_to_scrape: list - list of links to scrape in the next iteration
    updates:
        saved_pages: dict - dictionary of saved pages
        irrelevant_links: list - list of irrelevant links

    Note: saved_pages and irrelevant_links are updated in place.
    """
    # Initialize mutable defaults safely
    if irrelevant_links is None:
        irrelevant_links = []
    if saved_pages is None:
        saved_pages = {}
    if trusted_domains is None:
        trusted_domains = []
    if forbidden_url_parts is None:
        forbidden_url_parts = [
            "/get-help/",
            "about/",
            "/contact-us/",
            "/privacy/",
            "/terms/",
            "/blog/",
        ]

    logger.info("[Scrape:Loop] Iteration %s: Starting to scrape %s links", curr_iteration, len(links_to_scrape))
    content_types = await get_all_content_types(links_to_scrape)

    data_links = [
        url
        for url in content_types.keys()
        if "json" in content_types[url]
        or "yaml" in content_types[url]
        or "yml" in content_types[url]
        or "text/plain" in content_types[url]
    ]
    other_links = [url for url in content_types.keys() if url not in data_links]

    http_results = await scrape_all_data_pages(data_links)
    scrape_result = await scrape_urls(other_links)

    logger.info("[Scrape:Loop] Iteration %s: Scraped %s pages successfully", curr_iteration, len(scrape_result))

    new_links_to_scrape = []

    for scraped_link in scrape_result:
        if validate_pydantic_object(scraped_link.url, HttpUrl) and scraped_link.markdown is not None:
            content = scraped_link.markdown.fit_markdown
            contentType = "text/markdown"
            # with open("crawl_all.json", "a") as crawl_all_file:
            #     crawl_all_file.write(scraped_link.model_dump_json() + "\n")
            link_arr = get_links_for_page(scraped_link)
            link_arr_valid = [link for link in link_arr if validate_pydantic_object(link, HttpUrl)]
            page = SavedPage(
                url=scraped_link.url.rstrip("/"),
                contentType=contentType,
                content=content,
                links=link_arr_valid,
            )
            logger.debug("[Scrape:Loop] Extracted %s links from page %s", len(link_arr_valid), scraped_link.url)
            saved_pages[str(scraped_link.url)] = page
            new_links_to_scrape.extend(link_arr)

    for url, content in http_results:
        logger.debug("[Scrape:Loop] Loading %s as data file", str(url))
        if validate_pydantic_object(url, HttpUrl):
            page = SavedPage(
                url=url,
                contentType=content_types[str(url)],
                content=content,
                links=[],
            )
            logger.debug("[Scrape:Loop] Fetched data page %s", str(url))
            saved_pages[str(url)] = page
            # with open("crawl_all.json", "a") as crawl_all_file:
            #     crawl_all_file.write(page.model_dump_json() + "\n")

    logger.info(
        "[Scrape:Loop] Iteration %s: Extracted %s total new links from scraped pages",
        curr_iteration,
        len(new_links_to_scrape),
    )

    new_irrelevant_links, new_links_to_scrape = await filterOutIrrelevantLinks(
        links=new_links_to_scrape,
        saved_pages=saved_pages,
        trusted_domains=trusted_domains,
        app=app,
        app_version=app_version,
        past_irrelevant_links=irrelevant_links,
        forbidden_url_parts=forbidden_url_parts,
        llm_calls=max_iterations_filter_irrelevant,
    )

    irrelevant_links = new_irrelevant_links

    logger.info(
        "[Scrape:Loop] Iteration %s complete: %s relevant links to scrape next, %s total irrelevant links",
        curr_iteration,
        len(new_links_to_scrape),
        len(irrelevant_links),
    )

    return new_links_to_scrape


async def filterOutIrrelevantLinks(
    links: list[str],
    saved_pages: dict[str, SavedPage],
    trusted_domains: list[str],
    app: str,
    app_version: str,
    past_irrelevant_links: list[str],
    forbidden_url_parts: list[str],
    llm_calls: int = 5,
) -> tuple[list[str], list[str]]:
    """
    Filter out irrelevant links using multiple methods.
    1) Remove already evaluated links.
    2) Keep only links from trusted domains.
    3) Remove links containing forbidden URL parts.
    4) Use LLM to identify irrelevant links.
    inputs:
        links: list - list of links to filter
        saved_pages: dict - dictionary of already saved pages
        trusted_domains: list - list of trusted domains
        app: str - application name
        app_version: str - application version
        past_irrelevant_links: list - list of previously identified irrelevant links
        forbidden_url_parts: list - list of URL parts to filter out
        llm_calls: int - number of LLM calls to make
    outputs:
        list - list of irrelevant links from this run combined with past irrelevant links
        list - filtered list of relevant links
    """
    logger.info("[Scrape:Filter] Starting to filter %s links", len(links))
    links_set = set(links)
    current_links = links_set - set(saved_pages.keys())
    logger.info("[Scrape:Filter] After removing already scraped: %s links remain", len(current_links))

    current_links_trusted = [link for link in current_links if get_base_domain(link) in trusted_domains]
    logger.info("[Scrape:Filter] After filtering by trusted domains: %s links remain", len(current_links_trusted))

    current_links_trusted_valid = [link for link in current_links_trusted if validate_pydantic_object(link, HttpUrl)]
    logger.info("[Scrape:Filter] After validating URLs: %s links remain", len(current_links_trusted_valid))

    current_links_trusted_valid = list(set(current_links_trusted_valid) - set(past_irrelevant_links))
    logger.info(
        "[Scrape:Filter] After removing past irrelevant links and removing duplicates: %s links remain",
        len(current_links_trusted_valid),
    )

    links_to_remove = []
    for link in current_links_trusted_valid:
        for forbidden_part in forbidden_url_parts:
            if f"/{forbidden_part}/" in link:
                past_irrelevant_links.append(link)
                links_to_remove.append(link)
                break

    current_links_not_forbidden = [link for link in current_links_trusted_valid if link not in links_to_remove]
    logger.info(
        "[Scrape:Filter] After removing forbidden URL parts: %s links remain (removed %s)",
        len(current_links_not_forbidden),
        len(links_to_remove),
    )

    curr_run = 0
    while curr_run < llm_calls and len(current_links_not_forbidden) > 0:
        logger.info("[Scrape:Filter] Starting LLM filtering call %s/%s", curr_run + 1, llm_calls)

        irrelevant_prompts = get_irrelevant_filter_prompts(current_links_not_forbidden, app, app_version)

        irrelevant_llm_response = await get_irrelevant_llm_response(irrelevant_prompts)

        if irrelevant_llm_response is None:
            logger.warning("[Scrape:Filter] LLM filtering call %s/%s failed", curr_run + 1, llm_calls)
        else:
            logger.debug("[Scrape:Filter] LLM identified %s RAW irrelevant links", len(irrelevant_llm_response.links))
            irrelevant_llm_links = list(set(irrelevant_llm_response.links) & set(current_links_not_forbidden))
            logger.info("[Scrape:Filter] LLM identified %s irrelevant links", len(irrelevant_llm_links))
            logger.debug("[Scrape:Filter] LLM irrelevant links: %s", irrelevant_llm_links)

            past_irrelevant_links.extend(irrelevant_llm_links)

            current_links_not_forbidden = list(set(current_links_not_forbidden) - set(past_irrelevant_links))

        curr_run += 1

    logger.info(
        "[Scrape:Filter] Filtering complete: %s relevant links, %s total irrelevant links",
        len(current_links_not_forbidden),
        len(past_irrelevant_links),
    )

    return past_irrelevant_links, current_links_not_forbidden


# For now this function is not used, but may be useful later
def fetch_partial_attribute_links(html_content, url, partial_attribute_names=None) -> list:
    # based on content_scraping_strategy.py - _scrap starting at line 1566
    # based on content_scraping_strategy.py - _process_element starting at line 1106

    if partial_attribute_names is None:
        partial_attribute_names = ["data-"]

    extra_attributes = []
    extra_attributes_norm = []
    body = lhtml.document_fromstring(html_content)
    # Remove script and style tags
    for tag in ["script", "style", "link", "meta", "noscript"]:
        for element in body.xpath(f".//{tag}"):  # type: ignore
            if element.getparent() is not None:  # type: ignore
                element.getparent().remove(element)  # type: ignore

    for partial_attribute_name in partial_attribute_names:
        els = body.xpath(f'//*[@*[contains(name(), "{partial_attribute_name}")]]')  # type: ignore

        for element in els:  # type: ignore
            for attr_name, attr_value in element.attrib.items():  # type: ignore
                if partial_attribute_name in attr_name:
                    extra_attributes.append({attr_name: attr_value})
                    ref_norm = normalize_url(attr_value, url)
                    extra_attributes_norm.append({attr_name: ref_norm})

    return extra_attributes_norm


def clean_reference_list(reference_list):
    """
    Rework this function - works for now but not good for later.
    Only return the references which are either link or a relative reference that is not just '/'
    """
    return [
        link
        for link in reference_list
        if ("http" in link or "www" in link)
        or (link.startswith("/") and len(link) > 1)
        or (link.startswith("./") and len(link) > 2)
        or (link.startswith("../") and len(link) > 3)
    ]


def relative_paths_to_absolute(reference_list, current_url):
    """
    Scraped relative references are converted to absolute paths.
    The assumption here is that the current_url (i.e., the absolute path page we are currently scraping)
    is also the prefix of the relative path we are trying to reconstruct. This is a simple process of
    merging this current url (base) with the relative path scraped (using urljoin function).
    """

    new_reference_list = []

    # current_base_url = extract_base_url(current_url)

    for link in reference_list:
        if not ("http" in link or "www" in link):
            new_reference_list.append(urljoin(current_url, urlparse(link).path))
            # This is commented out, because for now, it may introduce too many irrelevant links
            # and the benefit may not be worth the additional delay in scraping.
            # new_reference_list.append(
            #     urljoin(current_base_url, urlparse(link).path)
            # )  # adding this because the relative references are not consistent
            # new_reference_list.append(
            #     urljoin(current_url, link)
            # )  # adding this because the relative references are not consistent
            # new_reference_list.append(
            #     urljoin(current_base_url, link)
            # )  # adding this because the relative references are not consistent
        else:
            new_reference_list.append(link)

    return new_reference_list


def extract_base_url(url: str):
    parsed_url = urlparse(url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

    return base_url


def get_links_for_page(scraperOutput: CrawlResult) -> list:
    """
    Extract and clean links from a CrawlResult object.
    inputs:
        scraperOutput: CrawlResult - the result object from the scraper
    outputs:
        list - cleaned list of absolute links
    """
    link_arr = []
    link_arr.extend([link["href"] for link in scraperOutput.links["internal"]])
    link_arr.extend([link["href"] for link in scraperOutput.links["external"]])
    link_arr_clean = clean_reference_list(link_arr)
    link_arr_abs = relative_paths_to_absolute(link_arr_clean, scraperOutput.url)
    return link_arr_abs


def get_file_extension(url: str) -> str:
    """
    Extract file extension from URL.
    inputs:
        url: str - the URL string
    outputs:
        str - file extension (without dot), or empty string if none
    """
    parsed_url = urlparse(url)
    path = parsed_url.path
    if "." in path:
        return path.split(".")[-1]
    return ""
