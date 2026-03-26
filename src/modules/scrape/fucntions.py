# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
import re
from typing import AsyncIterator, Awaitable, Callable, Dict, List, Tuple, cast
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

from ...common.chunk_processor.schema import SavedDocumentation
from ...common.schema import validate_pydantic_object
from ...config import config
from .llms import get_irrelevant_llm_response, get_relevant_links_from_text
from .prompts import get_irrelevant_filter_prompts, get_relevant_filter_prompts
from .schema import DocumentationReferences, IrrelevantLinks, ReferenceItem, RelevantLinks

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
    browser_config = BrowserConfig()  # accept_downloads=True, browser_type="firefox"
    run_config = CrawlerRunConfig(
        # user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.131 Safari/537.36",
        # simulate_user= True,
        check_robots_txt=True,
        # magic=True,
        wait_until="networkidle",
        delay_before_return_html=1.5,
        stream=True,
        # delay_before_return_html=10.0,
        # screenshot=True,
        markdown_generator=md_generator,
    )

    max_attempts = 3
    links_to_scrape = list(links_to_scrape_orig)
    scrape_out_success_count = 0
    # Was possibly unbound before
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


def process_citations_markdown(
    markdown_references: str, text_with_citations: str, documentation_url: str
) -> DocumentationReferences:
    """
    Parse crawl4ai citation markdown and extract ReferenceItem objects
    inputs:
        markdown_references: str - the markdown content with references
        text_with_citations: str - the text with citations
        documentation_url: str - the URL of the documentation the markdown was generated from.
    outputs:
        DocumentationReferences - the DocumentationReferences object containing the extracted references and citation markdown
    """

    ref_section_match = re.search(r"##\s+References\s*\n(.*)", markdown_references, re.DOTALL)
    references_markdown = ref_section_match.group(0).strip() if ref_section_match else markdown_references.strip()
    ref_block = ref_section_match.group(1) if ref_section_match else markdown_references

    pattern = re.compile(r"⟨(\d+)⟩\s+(https?://\S+?):[^\S\r\n]?(.+)?")
    references: list[ReferenceItem] = []
    for match in pattern.finditer(ref_block):
        number, url = int(match.group(1)), match.group(2)
        description = match.group(3).strip() if match.group(3) else ""
        references.append(ReferenceItem(number=number, url=url, description=description))

    return DocumentationReferences(
        documentation_url=documentation_url,
        references=references,
        references_markdown=references_markdown,
        text_with_citations=text_with_citations,
    )


def remove_citations(documentation: DocumentationReferences, urls: List[str]) -> DocumentationReferences:
    """
    Remove citations from markdown content and return updated DocumentationReferences.
    inputs:
        documentation: DocumentationReferences - the original DocumentationReferences object containing the markdown with citations
        urls: list - list of URLs to remove from the markdown
    outputs:
        DocumentationReferences - the updated DocumentationReferences object with citations removed from the markdown
    """

    updated_markdown = documentation.text_with_citations
    updated_citations_markdown = documentation.references_markdown
    for url in urls:
        matching = [
            r.number for r in documentation.references if r.url == url or r.url == url + "/" or r.url + "/" == url
        ]
        if not matching:
            logger.warning("[Scrape:Citations] URL %s not found in references, skipping citation removal", url)
            continue
        if len(matching) > 1:
            logger.warning(
                "[Scrape:Citations] URL %s has multiple citations in the references, removing only the first one (number %s)",
                url,
                matching[0],
            )
        url_no = matching[0]
        updated_citations_markdown = re.sub(rf"⟨{url_no}⟩.*(?:\n|$)", "", updated_citations_markdown)
        updated_markdown = re.sub(rf"\⟨{url_no}\⟩", "", updated_markdown)
        documentation.references = [ref for ref in documentation.references if ref.url != url]

    return DocumentationReferences(
        documentation_url=documentation.documentation_url,
        references=documentation.references,
        references_markdown=updated_citations_markdown,
        text_with_citations=updated_markdown,
    )


def update_references(documentation: DocumentationReferences, url_mapping: Dict[str, str]) -> DocumentationReferences:
    """
    Update citations in markdown content based on a mapping of old URLs to new URLs.
    inputs:
        documentation: DocumentationReferences - the original DocumentationReferences object containing the markdown with citations
        url_mapping: dict - a mapping of old URLs to new URLs for updating the citations
    outputs:
        DocumentationReferences - the updated DocumentationReferences object with citations updated in the markdown content
    """
    updated_markdown = documentation.references_markdown
    for old_url, new_url in url_mapping.items():
        pattern = rf"(⟨\d+⟩\s+){re.escape(old_url)}(:\s+)"
        updated_markdown = re.sub(pattern, rf"\1{new_url}\2", updated_markdown)
        documentation.references = [
            ReferenceItem(
                number=ref.number, url=new_url if ref.url == old_url else ref.url, description=ref.description
            )
            for ref in documentation.references
        ]

    return DocumentationReferences(
        documentation_url=documentation.documentation_url,
        references=documentation.references,
        references_markdown=updated_markdown,
        text_with_citations=documentation.text_with_citations,
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


def deduplicate_links(documentation_references: DocumentationReferences):
    """
    Remove duplicate links from a DocumentationReferences object.
    inputs:
        documentation_references: DocumentationReferences - the DocumentationReferences object to deduplicate
    outputs:
        Updates the DocumentationReference object in place
    """
    seen_urls = set()
    duplitcates: List[ReferenceItem] = []
    for ref in documentation_references.references:
        if ref.url not in seen_urls:
            seen_urls.add(ref.url)
        else:
            duplitcates.append(ref)
    for dup in duplitcates:
        min_number = min(ref.number for ref in documentation_references.references if ref.url == dup.url)
        other_numbers = [
            ref.number for ref in documentation_references.references if ref.url == dup.url and ref.number != min_number
        ]
        for num in other_numbers:
            documentation_references.references_markdown = re.sub(
                rf"⟨{num}⟩.*(?:\n|$)", "", documentation_references.references_markdown
            )
            documentation_references.text_with_citations = re.sub(
                rf"⟨{num}⟩", f"<{min_number}>", documentation_references.text_with_citations
            )
            documentation_references.references = [
                ref for ref in documentation_references.references if ref.number != num
            ]


async def scraper_loop(
    links_to_scrape: list[str],
    app: str,
    app_version: str,
    max_iterations_filter_irrelevant: int = 5,
    curr_iteration: int = 1,
    irrelevant_links: list[str] | None = None,
    saved_documentations: dict[str, SavedDocumentation] | None = None,
    trusted_domains: list[str] | None = None,
    forbidden_url_parts: list | None = None,
    last_iteration: bool = False,
    on_documentation_scraped: Callable[[SavedDocumentation], Awaitable[None]] | None = None,
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
        saved_documentations: dict - dictionary of already saved documentations
        trusted_domains: list - list of trusted domains
        forbidden_url_parts: list - list of URL parts to filter out
        last_iteration: bool - flag indicating if this is the last iteration of the scraper loop, on which we dont need to filter out irrelevant links
        on_documentation_scraped: optional async callback called immediately after a documentation is scraped and prepared for processing
    outputs:
        new_links_to_scrape: list - list of links to scrape in the next iteration
    updates:
        saved_documentations: dict - dictionary of saved documentations
        irrelevant_links: list - list of irrelevant links

    Note: saved_documentations and irrelevant_links are updated in place.
    """
    # Initialize mutable defaults safely
    if irrelevant_links is None:
        irrelevant_links = []
    if saved_documentations is None:
        saved_documentations = {}
    if trusted_domains is None:
        trusted_domains = []
    if forbidden_url_parts is None:
        forbidden_url_parts = [
            "get-help",
            "about",
            "contact-us",
            "privacy",
            "terms",
            "blog",
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

    http_results = await scrape_all_data_documentations(data_links)

    for url, content in http_results:
        if validate_pydantic_object(url, HttpUrl):
            if content == "error":
                other_links.append(
                    url
                )  # If fetching as data documentation failed, add to other links for regular scraping
                continue
            logger.debug("[Scrape:Loop] Loading %s as data file", str(url))
            documentation = SavedDocumentation(
                url=url,
                contentType=content_types[str(url)],
                content=content,
                links=[],
            )
            logger.debug("[Scrape:Loop] Fetched data documentation %s", str(url))
            saved_documentations[str(url)] = documentation
            if on_documentation_scraped:
                await on_documentation_scraped(documentation)

    new_links_to_scrape: List[str] = []
    scraped_documentations_count = 0
    current_scraped_urls = [url.rstrip("/") for url in other_links]

    async for scraped_link in scrape_urls(other_links):
        scraped_documentations_count += 1
        if validate_pydantic_object(scraped_link.url, HttpUrl) and scraped_link.markdown is not None:
            content = scraped_link.markdown.fit_markdown
            contentType = "text/markdown"
            documentation = SavedDocumentation(
                url=scraped_link.url.rstrip("/"),
                contentType=contentType,
                content=content,
                links=[],
            )
            if on_documentation_scraped:
                await on_documentation_scraped(documentation)

            documentation_references = process_citations_markdown(
                markdown_references=scraped_link.markdown.references_markdown,
                text_with_citations=scraped_link.markdown.markdown_with_citations,
                documentation_url=str(scraped_link.url),
            )

            link_arr = [ref.url for ref in documentation_references.references if ref.url]
            logger.info(
                f"[Scrape:Loop] Extracted {len(link_arr)} raw links from documentation %s", str(scraped_link.url)
            )
            link_arr_clean = clean_reference_list(link_arr)
            deleted_links = list(set(link_arr) - set(link_arr_clean))
            if deleted_links:
                documentation_references = remove_citations(documentation_references, deleted_links)
            link_arr_abs, map_of_links = relative_paths_to_absolute(link_arr_clean, str(scraped_link.url))
            documentation_references = update_references(documentation_references, map_of_links)
            deduplicate_links(documentation_references)
            links_without_anchors, anchor_url_mapping = remove_anchor_links(link_arr_abs)
            documentation_references = update_references(documentation_references, anchor_url_mapping)
            deduplicate_links(documentation_references)
            links_without_trailing_slash, map_without_trailing_slash = remove_trailing_slash(links_without_anchors)
            documentation_references = update_references(documentation_references, map_without_trailing_slash)
            deduplicate_links(documentation_references)
            link_arr_valid = [link for link in links_without_trailing_slash if validate_pydantic_object(link, HttpUrl)]
            link_arr_valid = list(set(link_arr_valid))
            removed_invalid_links = list(set(links_without_trailing_slash) - set(link_arr_valid))
            if removed_invalid_links:
                documentation_references = remove_citations(documentation_references, removed_invalid_links)
            if len(link_arr_valid) != len(documentation_references.references):
                logger.warning(
                    "[Scrape:Loop] After cleaning and validation, there is a mismatch between references and valid links for documentation %s. Number of valid links: %s, number of references: %s",
                    str(scraped_link.url),
                    len(link_arr_valid),
                    len(documentation_references.references),
                )
            # Probably we dont need to return the irrelevant links arr
            new_irrelevant_links, partly_filtered_new_links = await filterOutIrrelevantLinks(
                links=link_arr_valid,
                saved_documentations=saved_documentations,
                trusted_domains=trusted_domains,
                app=app,
                app_version=app_version,
                past_irrelevant_links=irrelevant_links,
                forbidden_url_parts=forbidden_url_parts,
                call_llm=False,
                current_scraped_urls=current_scraped_urls,
            )

            if len(partly_filtered_new_links) + len(new_irrelevant_links) != len(documentation_references.references):
                logger.warning(
                    "[Scrape:Loop] After irrelevant link filtering, there is a mismatch between references and valid+irrelevant links for documentation %s. Number of valid links: %s, number of irrelevant links: %s, number of references: %s",
                    str(scraped_link.url),
                    len(partly_filtered_new_links),
                    len(new_irrelevant_links),
                    len(documentation_references.references),
                )

            documentation_references_saved = documentation_references.model_copy()

            not_saved_or_current_irrelevant_links = [
                link
                for link in new_irrelevant_links
                if link not in saved_documentations
                and link + "/" not in saved_documentations
                and link not in current_scraped_urls
                and link + "/" not in current_scraped_urls
            ]

            documentation_references_saved = remove_citations(
                documentation_references_saved, not_saved_or_current_irrelevant_links
            )

            documentation_references = remove_citations(documentation_references, new_irrelevant_links)

            if len(partly_filtered_new_links) != len(documentation_references.references):
                logger.warning(
                    "[Scrape:Loop] After initial filtering, %s valid links remain but there are %s references for documentation %s",
                    len(partly_filtered_new_links),
                    len(documentation_references.references),
                    str(scraped_link.url),
                )

            if len(partly_filtered_new_links) > 0 or last_iteration:
                relevant_prompts = get_relevant_filter_prompts(
                    documentation_references.references_markdown, app, app_version
                )

                relevant_links_response: RelevantLinks | None = await get_relevant_links_from_text(relevant_prompts)

                relevant_links: List[str] = []
                if relevant_links_response:
                    relevant_links_raw = relevant_links_response.links if relevant_links_response.links else []
                    relevant_links, _ = remove_trailing_slash(relevant_links_raw)
                    hallucinated = [link for link in relevant_links if link not in partly_filtered_new_links]
                    if hallucinated:
                        logger.warning(
                            "[Scrape:Loop] LLM returned %s link(s) not present in extracted links on documentation %s, dropping: %s",
                            len(hallucinated),
                            str(scraped_link.url),
                            hallucinated,
                        )
                        relevant_links = [link for link in relevant_links if link in partly_filtered_new_links]
                        for link in hallucinated:
                            if link not in new_irrelevant_links:
                                logger.warning(
                                    "[Scrape:Loop] There is a mismatch between irrelevant and relevant links for URL %s: %s",
                                    str(scraped_link.url),
                                    link,
                                )
                    logger.info(
                        f"[Scrape:Loop] LLM identified {len(relevant_links)} relevant links on documentation %s",
                        str(scraped_link.url),
                    )
                    llm_irrelevant_links = list(set(partly_filtered_new_links) - set(relevant_links))
                    new_irrelevant_links.extend(llm_irrelevant_links)
                    # TODO: maybe we should do this only after all documentations are processed
                    irrelevant_links.extend(llm_irrelevant_links)
                    new_links_to_scrape.extend(relevant_links)
                    documentation_references = remove_citations(documentation_references, llm_irrelevant_links)
                    documentation_references_saved = remove_citations(
                        documentation_references_saved, llm_irrelevant_links
                    )

                new_links_to_scrape = list(set(new_links_to_scrape))

                logger.debug(
                    "[Scrape:Loop] Extracted %s valid links from documentation %s",
                    len(relevant_links),
                    scraped_link.url,
                )

                documentation.links = [HttpUrl(url=link) for link in relevant_links]
                documentation.documentationReferences = documentation_references_saved
                saved_documentations[str(scraped_link.url)] = documentation

            else:
                logger.info(
                    "[Scrape:Loop] No valid links found on documentation %s, skipping link extraction and saving content only",
                    str(scraped_link.url),
                )
                documentation.links = [
                    HttpUrl(url=ref.url) for ref in documentation_references_saved.references if ref.url
                ]
                documentation.documentationReferences = documentation_references_saved
                saved_documentations[str(scraped_link.url)] = documentation

    logger.info(
        "[Scrape:Loop] Iteration %s: Scraped %s documentations successfully",
        curr_iteration,
        scraped_documentations_count,
    )

    logger.info(
        "[Scrape:Loop] Iteration %s: Extracted %s total new links from scraped documentations",
        curr_iteration,
        len(new_links_to_scrape),
    )

    # new_irrelevant_links, new_links_to_scrape = await filterOutIrrelevantLinks(
    #     links=new_links_to_scrape,
    #     saved_documentations=saved_documentations,
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
    saved_documentations: dict[str, SavedDocumentation],
    trusted_domains: list[str],
    app: str,
    app_version: str,
    past_irrelevant_links: list[str],
    forbidden_url_parts: list[str],
    call_llm: bool = True,
    llm_calls: int = 5,
    current_scraped_urls: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """
    Filter out irrelevant links using multiple methods.
    1) Remove already evaluated links.
    2) Keep only links from trusted domains.
    3) Remove links containing forbidden URL parts.
    4) Use LLM to identify irrelevant links.
    inputs:
        links: list - list of links to filter
        saved_documentations: dict - dictionary of already saved documentations
        trusted_domains: list - list of trusted domains
        app: str - application name
        app_version: str - application version
        past_irrelevant_links: list - list of previously identified irrelevant links
        forbidden_url_parts: list - list of URL parts to filter out
        llm_calls: int - number of LLM calls to make
        current_scraped_urls: list - list of links already queued for the next iteration (to avoid duplicates)
    outputs:
        list - list of irrelevant links from this run
        list - filtered list of relevant links
    """
    links_set = set(links)
    logger.info("[Scrape:Filter] Starting to filter %s unique links", len(links_set))
    current_links = [
        link
        for link in list(links_set)
        if link not in saved_documentations
        and link + "/" not in saved_documentations
        and (
            current_scraped_urls is None
            or (link not in current_scraped_urls and link + "/" not in current_scraped_urls)
        )
    ]
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
    past_irrelevant_links.extend(list(set(current_links_past_filtered) - set(current_links_trusted_valid)))

    links_to_remove = []
    for link in current_links_trusted_valid:
        for forbidden_part in forbidden_url_parts:
            if (
                f"/{forbidden_part}/" in link
                or f".{forbidden_part}." in link
                or link.endswith(f"/{forbidden_part}")
                or link.endswith(f".{forbidden_part}")
            ):
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

    new_irrelevant_links = list(links_set - set(current_links_not_forbidden))

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


def remove_trailing_slash(urls: List[str]) -> Tuple[List[str], Dict[str, str]]:
    """
    Remove trailing slash from references if they exist, to avoid duplicates and inconsistencies in URL formatting.

    """
    new_urls = []
    map_of_links = {}
    for url in urls:
        if url.endswith("/"):
            map_of_links[url] = url[:-1]
            new_urls.append(url[:-1])
        else:
            new_urls.append(url)
    return new_urls, map_of_links


def relative_paths_to_absolute(reference_list: List[str], current_url: str) -> Tuple[List[str], Dict[str, str]]:
    """
    Scraped relative references are converted to absolute paths.
    The assumption here is that the current_url (i.e., the absolute path documentation we are currently scraping)
    is also the prefix of the relative path we are trying to reconstruct. This is a simple process of
    merging this current url (base) with the relative path scraped (using urljoin function).

    inputs:
        reference_list: list - list of scraped references (links) which may be relative or absolute
        current_url: str - the URL of the documentation currently being scraped, used as the base for converting relative paths
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


def get_links_for_documentation(scraperOutput: CrawlResult) -> list:
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
