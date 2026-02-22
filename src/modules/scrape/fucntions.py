# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
import re
from typing import Dict, List, Tuple, cast
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
from ...config import config
from .llms import get_irrelevant_llm_response, get_relevant_links_from_text
from .prompts import get_irrelevant_filter_prompts, get_relevant_filter_prompts
from .schema import IrrelevantLinks, PageReferences, ReferenceItem, RelevantLinks

logger = logging.getLogger(__name__)


async def scrape_urls(links_to_scrape_orig: list[str]) -> list[CrawlResult]:
    """
    Scrape URLs and return successful CrawlResult objects.
    Retries failed URLs up to `max_attempts` times.
    """
    logger.info("[Scrape:URLs] Starting to scrape %s URLs", len(links_to_scrape_orig))
    prune_filter = PruningContentFilter(threshold=0.42, threshold_type="dynamic", min_word_threshold=1)
    md_generator = DefaultMarkdownGenerator(
        content_filter=prune_filter, options={"ignore_images": True, "skip_internal_links": True}
    )
    browser_config = BrowserConfig(browser_type="firefox")  # accept_downloads=True, browser_type="firefox"
    run_config = CrawlerRunConfig(
        # user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.131 Safari/537.36",
        # simulate_user= True,
        check_robots_txt=True,
        # magic=True,
        wait_until="networkidle",
        delay_before_return_html=1.5,
        # delay_before_return_html=10.0,
        # screenshot=True,
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


async def fetch_data_page(url: str) -> tuple[str, str] | None:
    """
    Fetch the content of a data page (e.g., JSON, YAML).
    inputs:
        url: str - the URL to fetch
    outputs:
        tuple[str, str] | None - the tuple of the url and the content of the page or None if failed
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
                            "[Scrape:DataPage] URL %s has unsupported content type: %s, defaulting to crawl4ai scraping",
                            url,
                            content_type,
                        )
                        return (url, "error")
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
        list - list of tuples mapping URL to its content
    """
    tasks = [fetch_data_page(link) for link in links]
    results = await asyncio.gather(*tasks)
    return [result for result in results if result is not None]


def process_citations_markdown(markdown_references: str, text_with_citations: str, page_url: str) -> PageReferences:
    """
    Parse crawl4ai citation markdown and extract ReferenceItem objects
    inputs:
        markdown_references: str - the markdown content with references
        text_with_citations: str - the text with citations
        page_url: str - the URL of the page the markdown was generated from.
    outputs:
        PageReferences - the PageReferences object containing the extracted references and citation markdown
    """

    ref_section_match = re.search(r"##\s+References\s*\n(.*)", markdown_references, re.DOTALL)
    references_markdown = ref_section_match.group(0).strip() if ref_section_match else markdown_references.strip()
    ref_block = ref_section_match.group(1) if ref_section_match else markdown_references

    pattern = re.compile(r"⟨(\d+)⟩\s+(https?://\S+?):\s+(.+)")
    references: list[ReferenceItem] = []
    for match in pattern.finditer(ref_block):
        number, url, description = int(match.group(1)), match.group(2), match.group(3).strip()
        references.append(ReferenceItem(number=number, url=url, description=description))

    return PageReferences(
        page_url=page_url,
        references=references,
        references_markdown=references_markdown,
        text_with_citations=text_with_citations,
    )


def remove_citations(page: PageReferences, urls: List[str]) -> PageReferences:
    """
    Remove citations from markdown content and return updated PageReferences.
    inputs:
        page: PageReferences - the original PageReferences object containing the markdown with citations
        urls: list - list of URLs to remove from the markdown
    outputs:
        PageReferences - the updated PageReferences object with citations removed from the markdown
    """

    updated_markdown = page.text_with_citations
    updated_citations_markdown = page.references_markdown
    for url in urls:
        url_no = [r.number for r in page.references if r.url == url][0]
        updated_citations_markdown = re.sub(rf"⟨{url_no}⟩.*\n", "", updated_citations_markdown)
        updated_markdown = re.sub(rf"\⟨{url_no}\⟩", "", updated_markdown)

    return PageReferences(
        page_url=page.page_url,
        references=page.references,
        references_markdown=updated_citations_markdown,
        text_with_citations=updated_markdown,
    )


def update_references(page: PageReferences, url_mapping: Dict[str, str]) -> PageReferences:
    """
    Update citations in markdown content based on a mapping of old URLs to new URLs.
    inputs:
        page: PageReferences - the original PageReferences object containing the markdown with citations
        url_mapping: dict - a mapping of old URLs to new URLs for updating the citations
    outputs:
        PageReferences - the updated PageReferences object with citations updated in the markdown content
    """
    updated_markdown = page.references_markdown
    for old_url, new_url in url_mapping.items():
        updated_markdown = re.sub(rf"{re.escape(old_url)}", f"{new_url}", updated_markdown)
        page.references = [
            ReferenceItem(
                number=ref.number, url=new_url if ref.url == old_url else ref.url, description=ref.description
            )
            for ref in page.references
        ]

    return PageReferences(
        page_url=page.page_url,
        references=page.references,
        references_markdown=updated_markdown,
        text_with_citations=page.text_with_citations,
    )


def remove_anchor_links(urls: list[str]) -> Tuple[List[str], Dict[str, str]]:
    """
    TODO: Temporary solution
    Remove anchor links from a list of URLs and return a mapping of original URLs to cleaned URLs.
    inputs:
        urls: list - list of URLs to clean
    outputs:
        tuple - (cleaned_urls, url_mapping) where:
            cleaned_urls: list - list of URLs with anchor links removed
            url_mapping: dict - mapping of original URLs to cleaned URLs for reference
    """
    cleaned_urls = []
    url_mapping = {}
    for url in urls:
        cleaned_url = url.split("#")[0]
        cleaned_urls.append(cleaned_url)
        url_mapping[url] = cleaned_url
    return cleaned_urls, url_mapping


def deduplicate_links(page_references: PageReferences):
    """
    Remove duplicate links from a PageReferences object.
    inputs:
        page_references: PageReferences - the PageReferences object to deduplicate
    outputs:
        Updates the PageReference object in place
    """
    seen_urls = set()
    duplitcates: List[ReferenceItem] = []
    for ref in page_references.references:
        if ref.url not in seen_urls:
            seen_urls.add(ref.url)
        else:
            duplitcates.append(ref)
    for dup in duplitcates:
        min_number = min(ref.number for ref in page_references.references if ref.url == dup.url)
        other_numbers = [
            ref.number for ref in page_references.references if ref.url == dup.url and ref.number != min_number
        ]
        for num in other_numbers:
            page_references.references_markdown = re.sub(rf"⟨{num}⟩.*\n", "", page_references.references_markdown)
            page_references.text_with_citations = re.sub(
                rf"\⟨{num}\⟩", f"<{min_number}>", page_references.text_with_citations
            )


async def scraper_loop(
    links_to_scrape: list[str],
    app: str,
    app_version: str,
    max_iterations_filter_irrelevant: int = 5,
    curr_iteration: int = 1,
    irrelevant_links: list[str] | None = None,
    saved_pages: dict[str, SavedPage] | None = None,
    trusted_domains: list[str] | None = None,
    forbidden_url_parts: list | None = None,
    last_iteration: bool = False,
):
    """
    Main scraper loop to scrape links, filter irrelevant ones, and process html content.
    inputs:
        links_to_scrape: list - list of links to scrape
        app: str - application name
        app_version: str - application version
        max_iterations_filter_irrelevant: int - maximum iterations for filtering irrelevant links
        curr_iteration: int - current iteration count
        irrelevant_links: list - list of already identified irrelevant links
        saved_pages: dict - dictionary of already saved pages
        trusted_domains: list - list of trusted domains
        forbidden_url_parts: list - list of URL parts to filter out
        last_iteration: bool - flag indicating if this is the last iteration of the scraper loop, on which we dont need to filter out irrelevant links
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

    for url, content in http_results:
        if validate_pydantic_object(url, HttpUrl):
            if content == "error":
                other_links.append(url)  # If fetching as data page failed, add to other links for regular scraping
                continue
            logger.debug("[Scrape:Loop] Loading %s as data file", str(url))
            page = SavedPage(
                url=url,
                contentType=content_types[str(url)],
                content=content,
                links=[],
            )
            logger.debug("[Scrape:Loop] Fetched data page %s", str(url))
            saved_pages[str(url)] = page

    scrape_result = await scrape_urls(other_links)

    logger.info("[Scrape:Loop] Iteration %s: Scraped %s pages successfully", curr_iteration, len(scrape_result))

    new_links_to_scrape: List[str] = []

    for scraped_link in scrape_result:
        if validate_pydantic_object(scraped_link.url, HttpUrl) and scraped_link.markdown is not None:
            content = scraped_link.markdown.fit_markdown
            # logger.info(f"[Scrape:Loop] markdown with citations: {scraped_link.markdown.markdown_with_citations}")
            # logger.info(f"[Scrape:Loop] references: {scraped_link.markdown.references_markdown}")
            contentType = "text/markdown"

            # with open("crawl_all.json", "a") as crawl_all_file:
            #     crawl_all_file.write(scraped_link.model_dump_json() + "\n")
            page_references = process_citations_markdown(
                markdown_references=scraped_link.markdown.references_markdown,
                text_with_citations=scraped_link.markdown.markdown_with_citations,
                page_url=str(scraped_link.url),
            )
            if not last_iteration:
                link_arr = [ref.url for ref in page_references.references if ref.url]
                logger.info(f"[Scrape:Loop] Extracted {len(link_arr)} raw links from page %s", str(scraped_link.url))
                link_arr_clean = clean_reference_list(link_arr)
                deleted_links = list(set(link_arr) - set(link_arr_clean))
                if deleted_links:
                    page_references = remove_citations(page_references, deleted_links)
                link_arr_abs, map_of_links = relative_paths_to_absolute(link_arr_clean, str(scraped_link.url))
                page_references = update_references(page_references, map_of_links)
                deduplicate_links(page_references)
                links_without_anchors, anchor_url_mapping = remove_anchor_links(link_arr_abs)
                page_references = update_references(page_references, anchor_url_mapping)
                deduplicate_links(page_references)
                link_arr_valid = [link for link in links_without_anchors if validate_pydantic_object(link, HttpUrl)]
                # Probably we dont need to return the irrelevant links arr
                new_irrelevant_links, partly_filtered_new_links = await filterOutIrrelevantLinks(
                    links=link_arr_valid,
                    saved_pages=saved_pages,
                    trusted_domains=trusted_domains,
                    app=app,
                    app_version=app_version,
                    past_irrelevant_links=irrelevant_links,
                    forbidden_url_parts=forbidden_url_parts,
                    call_llm=False,
                )

                if len(partly_filtered_new_links) > 0:
                    already_evaluated = list(set(partly_filtered_new_links) & set(new_links_to_scrape))

                    evaluated_and_irrelevant = already_evaluated + new_irrelevant_links

                    page_references = remove_citations(page_references, evaluated_and_irrelevant)

                    relevant_prompts = get_relevant_filter_prompts(
                        page_references.references_markdown, app, app_version
                    )

                    relevant_links_response: RelevantLinks | None = await get_relevant_links_from_text(relevant_prompts)

                    relevant_links = []
                    if relevant_links_response:
                        relevant_links = relevant_links_response.links if relevant_links_response.links else []
                        logger.info(
                            f"[Scrape:Loop] LLM identified {len(relevant_links)} relevant links on page %s",
                            str(scraped_link.url),
                        )
                        llm_irrelevant_links = list(
                            set(partly_filtered_new_links) - set(relevant_links) - set(already_evaluated)
                        )
                        new_irrelevant_links.extend(llm_irrelevant_links)
                        # TODO: maybe we should do this only after all pages are processed
                        irrelevant_links.extend(llm_irrelevant_links)
                        new_links_to_scrape.extend(relevant_links)
                        page_references = remove_citations(page_references, llm_irrelevant_links)

                    new_links_to_scrape = list(set(new_links_to_scrape))

                    logger.debug(
                        "[Scrape:Loop] Extracted %s valid links from page %s", len(relevant_links), scraped_link.url
                    )

                    page = SavedPage(
                        url=scraped_link.url.rstrip("/"),
                        contentType=contentType,
                        content=content,
                        links=[HttpUrl(url=link) for link in relevant_links],
                        pageReferences=page_references,
                    )
                    saved_pages[str(scraped_link.url)] = page

                else:
                    logger.info(
                        "[Scrape:Loop] No valid links found on page %s, skipping link extraction and saving content only",
                        str(scraped_link.url),
                    )
                    page = SavedPage(
                        url=scraped_link.url.rstrip("/"),
                        contentType=contentType,
                        content=content,
                        links=[],
                        pageReferences=page_references,
                    )
                    saved_pages[str(scraped_link.url)] = page

            else:
                page = SavedPage(
                    url=scraped_link.url.rstrip("/"),
                    contentType=contentType,
                    content=content,
                    links=[HttpUrl(url=url.url) for url in page_references.references if url.url],
                    pageReferences=page_references,
                )
                saved_pages[str(scraped_link.url)] = page

    logger.info(
        "[Scrape:Loop] Iteration %s: Extracted %s total new links from scraped pages",
        curr_iteration,
        len(new_links_to_scrape),
    )

    # new_irrelevant_links, new_links_to_scrape = await filterOutIrrelevantLinks(
    #     links=new_links_to_scrape,
    #     saved_pages=saved_pages,
    #     trusted_domains=trusted_domains,
    #     app=app,
    #     app_version=app_version,
    #     past_irrelevant_links=irrelevant_links,
    #     forbidden_url_parts=forbidden_url_parts,
    #     llm_calls=max_iterations_filter_irrelevant,
    # )

    # irrelevant_links = new_irrelevant_links

    logger.info(
        "[Scrape:Loop] Iteration %s complete: %s relevant links to scrape next, %s total irrelevant links",
        curr_iteration,
        len(new_links_to_scrape),
        len(irrelevant_links),
    )

    return new_links_to_scrape


async def processIrrelevantLinksPart(
    irrelevant_links_part: list[str], app: str, app_version: str
) -> IrrelevantLinks | None:
    irrelevant_prompts = get_irrelevant_filter_prompts(irrelevant_links_part, app, app_version)
    return await get_irrelevant_llm_response(irrelevant_prompts)


async def filterOutIrrelevantLinks(
    links: list[str],
    saved_pages: dict[str, SavedPage],
    trusted_domains: list[str],
    app: str,
    app_version: str,
    past_irrelevant_links: list[str],
    forbidden_url_parts: list[str],
    call_llm: bool = True,
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
        list - list of irrelevant links from this run
        list - filtered list of relevant links
    """
    links_set = set(links)
    logger.info("[Scrape:Filter] Starting to filter %s unique links", len(links_set))
    current_links = links_set - set(saved_pages.keys())
    logger.info("[Scrape:Filter] After removing already scraped: %s links remain", len(current_links))

    current_links_past_filtered = list(set(current_links) - set(past_irrelevant_links))
    logger.info(
        "[Scrape:Filter] After removing past irrelevant links: %s links remain",
        len(current_links_past_filtered),
    )

    current_links_trusted = [
        link
        for link in current_links_past_filtered
        if get_base_domain(link) in trusted_domains or "netsuite" in get_base_domain(link)
    ]
    logger.info("[Scrape:Filter] After filtering by trusted domains: %s links remain", len(current_links_trusted))

    current_links_trusted_valid = [link for link in current_links_trusted if validate_pydantic_object(link, HttpUrl)]
    logger.info("[Scrape:Filter] After validating URLs: %s links remain", len(current_links_trusted_valid))
    past_irrelevant_links.extend(list(set(current_links_trusted_valid) - set(current_links_past_filtered)))

    links_to_remove = []
    for link in current_links_trusted_valid:
        for forbidden_part in forbidden_url_parts:
            if f"/{forbidden_part}/" in link or f".{forbidden_part}." in link:
                past_irrelevant_links.append(link)
                links_to_remove.append(link)
                break

    current_links_not_forbidden = [link for link in current_links_trusted_valid if link not in links_to_remove]
    logger.info(
        "[Scrape:Filter] After removing forbidden URL parts: %s links remain (removed %s)",
        len(current_links_not_forbidden),
        len(links_to_remove),
    )

    if not call_llm:
        new_irrelevant_links = list(links_set - set(current_links_not_forbidden))
        return new_irrelevant_links, current_links_not_forbidden

    curr_run = 0
    while curr_run < llm_calls and len(current_links_not_forbidden) > 0:
        logger.info("[Scrape:Filter] Starting LLM filtering call %s/%s", curr_run + 1, llm_calls)

        link_parts: List[List[str]] = []

        step = len(current_links_not_forbidden) / config.scrape_and_process.irrelevant_links_parts
        number_of_steps = min(
            config.scrape_and_process.irrelevant_links_parts_min_length,
            max(1, int(len(current_links_not_forbidden) / step)),
        )
        for i in range(number_of_steps):
            part_links = current_links_not_forbidden[
                int(i * step) : min(int((i + 1) * step), len(current_links_not_forbidden))
            ]
            link_parts.append(part_links)

        irrelevant_llm_responses = await asyncio.gather(
            *[processIrrelevantLinksPart(part, app, app_version) for part in link_parts]
        )

        if any(resp is None for resp in irrelevant_llm_responses):
            logger.warning("[Scrape:Filter] LLM filtering call %s/%s failed", curr_run + 1, llm_calls)
        else:
            irrelevant_llm_links = []
            for resp in irrelevant_llm_responses:
                if resp is not None:
                    irrelevant_llm_links.extend(resp.links)
            logger.debug("[Scrape:Filter] LLM identified %s RAW irrelevant links", len(irrelevant_llm_links))
            irrelevant_llm_links = list(set(irrelevant_llm_links) & set(current_links_not_forbidden))
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

    new_irrelevant_links = list(set(links_set - set(current_links_not_forbidden)))

    return new_irrelevant_links, current_links_not_forbidden


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


def relative_paths_to_absolute(reference_list: List[str], current_url: str) -> Tuple[List[str], Dict[str, str]]:
    """
    Scraped relative references are converted to absolute paths.
    The assumption here is that the current_url (i.e., the absolute path page we are currently scraping)
    is also the prefix of the relative path we are trying to reconstruct. This is a simple process of
    merging this current url (base) with the relative path scraped (using urljoin function).

    inputs:
        reference_list: list - list of scraped references (links) which may be relative or absolute
        current_url: str - the URL of the page currently being scraped, used as the base for converting relative paths
    outputs:
        tuple - (new_reference_list, map_of_links) where:
            new_reference_list: list - list of absolute URLs after conversion
            map_of_links: dict - mapping of original reference to its absolute URL for relative references
    """

    new_reference_list = []

    # current_base_url = extract_base_url(current_url)

    map_of_links = {}

    for link in reference_list:
        if not ("http" in link or "www" in link):
            map_of_links[link] = urljoin(current_url, urlparse(link).path)
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

    return new_reference_list, map_of_links


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
    link_arr_abs, _ = relative_paths_to_absolute(link_arr_clean, scraperOutput.url)
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
