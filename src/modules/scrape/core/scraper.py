# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Awaitable, Callable, List

from pydantic import HttpUrl

from src.common.documentation import DocumentationReferences, SavedDocumentation
from src.common.schema import validate_pydantic_object
from src.common.web import (
    get_all_content_types,
    scrape_all_data_documentations,
    scrape_urls,
)
from src.config import config
from src.modules.scrape.core.citations import (
    deduplicate_links,
    process_citations_markdown,
    remove_citations,
    update_references,
)
from src.modules.scrape.core.link_filter import filter_out_irrelevant_links
from src.modules.scrape.core.links import (
    clean_reference_list,
    is_forbidden_url,
    relative_paths_to_absolute,
    remove_anchor_links,
    remove_trailing_slash,
)
from src.modules.scrape.core.llms import get_relevant_links_from_text
from src.modules.scrape.prompts.prompts import get_relevant_filter_prompts
from src.modules.scrape.schema import RelevantLinks

logger = logging.getLogger(__name__)


async def scraper_loop(
    links_to_scrape: list[str],
    app: str,
    app_version: str,
    curr_iteration: int = 1,
    irrelevant_links: list[str] | None = None,
    saved_documentations: dict[str, SavedDocumentation] | None = None,
    trusted_domains: list[str] | None = None,
    forbidden_url_parts: list[str] | None = None,
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

    logger.info("[Scrape:Loop] Iteration %s: Starting with %s candidate links", curr_iteration, len(links_to_scrape))
    links_before_forbidden_filter = len(links_to_scrape)
    links_to_scrape = [link for link in links_to_scrape if not is_forbidden_url(link, forbidden_url_parts)]
    if len(links_to_scrape) != links_before_forbidden_filter:
        logger.info(
            "[Scrape:Loop] Iteration %s: Skipped %s candidate links matching forbidden URL parts",
            curr_iteration,
            links_before_forbidden_filter - len(links_to_scrape),
        )
    if not links_to_scrape:
        logger.info("[Scrape:Loop] Iteration %s: No candidate links left after forbidden URL filtering", curr_iteration)
        return []

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
    logger.info(
        "[Scrape:Loop] Iteration %s: Link type split -> %s data-file links, %s HTML links",
        curr_iteration,
        len(data_links),
        len(other_links),
    )

    http_results = await scrape_all_data_documentations(data_links)
    data_documentations_count = 0
    data_fallback_to_html_count = 0

    for url, content in http_results:
        if validate_pydantic_object(url, HttpUrl):
            if content == "error":
                other_links.append(
                    url
                )  # If fetching as data documentation failed, add to other links for regular scraping
                data_fallback_to_html_count += 1
                continue
            logger.debug("[Scrape:Loop] Loading %s as data file", str(url))
            documentation = SavedDocumentation(
                url=url,
                content_type=content_types[str(url)],
                content=content,
                links=[],
            )
            logger.debug("[Scrape:Loop] Fetched data documentation %s", str(url))
            saved_documentations[str(url)] = documentation
            data_documentations_count += 1
            if on_documentation_scraped:
                await on_documentation_scraped(documentation)

    if data_fallback_to_html_count:
        logger.debug(
            "[Scrape:Loop] Iteration %s: %s data-file links fell back to HTML scraping",
            curr_iteration,
            data_fallback_to_html_count,
        )

    new_links_to_scrape: List[str] = []
    scraped_documentations_count = 0
    current_scraped_urls = [url.rstrip("/") for url in other_links]

    logger.info(
        "[Scrape:Loop] Iteration %s: Starting HTML scrape for %s URLs",
        curr_iteration,
        len(other_links),
    )

    async for scraped_link in scrape_urls(other_links):
        scraped_documentations_count += 1
        if validate_pydantic_object(scraped_link.url, HttpUrl) and scraped_link.markdown is not None:
            content = scraped_link.markdown.fit_markdown
            content_type = "text/markdown"
            documentation = SavedDocumentation(
                url=scraped_link.url.rstrip("/"),
                content_type=content_type,
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
            max_links = config.scrape_and_process.max_links_per_documentation
            if max_links > 0 and len(link_arr) > max_links:
                logger.warning(
                    "[Scrape:Loop] Documentation %s has %s raw links, exceeding limit %s. "
                    "Saving content without processing outgoing links.",
                    str(scraped_link.url),
                    len(link_arr),
                    max_links,
                )
                documentation.documentation_references = DocumentationReferences(
                    documentation_url=str(scraped_link.url),
                    references=[],
                    references_markdown="",
                    text_with_citations=content,
                )
                saved_documentations[str(scraped_link.url)] = documentation
                continue

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
            new_irrelevant_links, partly_filtered_new_links = await filter_out_irrelevant_links(
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
                documentation.documentation_references = documentation_references_saved
                saved_documentations[str(scraped_link.url)] = documentation

            else:
                logger.info(
                    "[Scrape:Loop] No valid links found on documentation %s, skipping link extraction and saving content only",
                    str(scraped_link.url),
                )
                documentation.links = [
                    HttpUrl(url=ref.url) for ref in documentation_references_saved.references if ref.url
                ]
                documentation.documentation_references = documentation_references_saved
                saved_documentations[str(scraped_link.url)] = documentation

    logger.info(
        "[Scrape:Loop] Iteration %s: Scraped %s HTML documentations successfully",
        curr_iteration,
        scraped_documentations_count,
    )
    logger.info(
        "[Scrape:Loop] Iteration %s: Processed %s documentations in total (%s data-file, %s HTML)",
        curr_iteration,
        data_documentations_count + scraped_documentations_count,
        data_documentations_count,
        scraped_documentations_count,
    )

    logger.info(
        "[Scrape:Loop] Iteration %s: Extracted %s total new links from scraped documentations",
        curr_iteration,
        len(new_links_to_scrape),
    )

    logger.info(
        "[Scrape:Loop] Iteration %s complete: %s relevant links to scrape next, %s total irrelevant links",
        curr_iteration,
        len(new_links_to_scrape),
        len(irrelevant_links),
    )

    return new_links_to_scrape
