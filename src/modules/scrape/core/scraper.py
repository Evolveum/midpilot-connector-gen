# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
from collections.abc import Collection
from typing import Any, Awaitable, Callable, List

from crawl4ai.utils import get_base_domain  # type: ignore
from pydantic import HttpUrl

from src.config import config
from src.core.schema import validate_pydantic_object
from src.documents import DocumentationReferences, RetrievalMetadata, SavedDocumentation, SpecRetrievalMetadata
from src.documents.spec_validation import SpecDescription, describe_spec, parse_structured_document
from src.integrations.web import (
    get_all_content_types,
    scrape_all_data_documentations,
    scrape_urls,
)
from src.integrations.web.page_content import extract_main_content
from src.integrations.web.spec_discovery import discover_spec_urls
from src.integrations.web.urls import is_forbidden_url
from src.modules.scrape.core.citations import (
    deduplicate_links,
    process_citations_markdown,
    remove_citations,
    update_references,
)
from src.modules.scrape.core.link_filter import filter_out_irrelevant_links
from src.modules.scrape.core.links import (
    clean_reference_list,
    relative_paths_to_absolute,
    remove_anchor_links,
    remove_trailing_slash,
)
from src.modules.scrape.core.llms import get_relevant_links_from_text
from src.modules.scrape.prompts.prompts import get_relevant_filter_prompts
from src.modules.scrape.schema import RelevantLinks

logger = logging.getLogger(__name__)


# crawl4ai reports markdown generation failures inside the markdown string itself.
_CRAWL4AI_MARKDOWN_ERROR_PREFIXES = ("Error converting HTML to markdown", "Error generating fit markdown")


def _is_usable_content(content: str | None) -> bool:
    """Return False for empty content or a crawl4ai markdown error message, which must never be saved."""
    if content is None or not content.strip():
        return False
    return not content.lstrip().startswith(_CRAWL4AI_MARKDOWN_ERROR_PREFIXES)


def _inspect_data_documentation(content: str) -> tuple[bool, SpecDescription | None]:
    """Return whether the content is JSON/YAML and which API description it is, if any. CPU-bound."""
    document = parse_structured_document(content)
    if document is None:
        return False, None
    return True, describe_spec(document)


async def _save_data_documentation(
    url: str,
    content: str,
    content_type: str,
    *,
    starter: bool,
    spec_required: bool,
    saved_documentations: dict[str, SavedDocumentation],
    on_documentation_scraped: Callable[[SavedDocumentation], Awaitable[None]] | None,
) -> bool:
    """Save a fetched data file when it is worth keeping; return whether it was saved.

    API descriptions are always kept. Other JSON/YAML files (``package.json``, translation bundles) are
    kept only as starter links the user picked. Plain text (a raw markdown file) is kept unless the URL
    was referenced by a page as its API description (``spec_required``).
    """
    if not _is_usable_content(content):
        logger.warning("[Scrape:Loop] Data documentation %s is empty, not saving it", url)
        return False
    structured, spec = await asyncio.to_thread(_inspect_data_documentation, content)
    if spec is None and not starter and (structured or spec_required):
        logger.info("[Scrape:Loop] Data documentation %s is not an API description, not saving it", url)
        return False
    documentation = SavedDocumentation(
        url=url,
        content_type=content_type,
        content=content,
        links=[],
        retrieval=RetrievalMetadata(
            starter=starter, spec=SpecRetrievalMetadata.from_description(spec) if spec is not None else None
        ),
    )
    if spec is not None:
        logger.info(
            "[Scrape:Loop] Saving %s document %s (title %r, version %r)", spec.format, url, spec.title, spec.version
        )
    saved_documentations[url] = documentation
    if on_documentation_scraped:
        await on_documentation_scraped(documentation)
    return True


async def _save_referenced_specs(
    spec_urls: list[str],
    *,
    trusted_domains: list[str],
    saved_documentations: dict[str, SavedDocumentation],
    on_documentation_scraped: Callable[[SavedDocumentation], Awaitable[None]] | None,
) -> int:
    """Fetch the API descriptions the scraped pages reference (one hop, never crawled further).

    Only specs on the trusted domains are fetched, plus raw GitHub files: vendors often publish the spec
    in their repository and link it from their documentation. Only a recognized API description is
    saved. Returns the number of saved specs.
    """
    spec_domains = {*trusted_domains, "githubusercontent.com"}
    candidates = [url for url in dict.fromkeys(spec_urls) if url not in saved_documentations]
    in_scope = [url for url in candidates if get_base_domain(url) in spec_domains]
    if len(in_scope) != len(candidates):
        logger.info(
            "[Scrape:Loop] Skipping %s referenced API descriptions outside the trusted domains",
            len(candidates) - len(in_scope),
        )
    if not in_scope:
        return 0

    content_types = await get_all_content_types(in_scope)
    saved_count = 0
    for url, content in await scrape_all_data_documentations(in_scope):
        if content == "error":
            logger.info("[Scrape:Loop] Referenced API description %s is not served as a data file, skipping it", url)
            continue
        if await _save_data_documentation(
            url,
            content,
            content_types.get(url, ""),
            starter=False,
            spec_required=True,
            saved_documentations=saved_documentations,
            on_documentation_scraped=on_documentation_scraped,
        ):
            saved_count += 1
    return saved_count


def _page_link_urls(result: Any) -> list[str]:
    """Absolute hrefs crawl4ai found on the page, navigation included."""
    links = getattr(result, "links", None) or {}
    return [
        str(link["href"])
        for group in ("internal", "external")
        for link in links.get(group, [])
        if isinstance(link, dict) and link.get("href")
    ]


def _clean_documentation_links(
    documentation_references: DocumentationReferences, link_arr: list[str], documentation_url: str
) -> tuple[DocumentationReferences, list[str]]:
    """
    Normalize the outgoing links of one scraped documentation and keep its citations in sync.
    CPU-bound on pages with thousands of links, so scraper_loop runs it off the event loop.
    """
    link_arr_clean = clean_reference_list(link_arr)
    deleted_links = list(set(link_arr) - set(link_arr_clean))
    if deleted_links:
        documentation_references = remove_citations(documentation_references, deleted_links)
    link_arr_abs, map_of_links = relative_paths_to_absolute(link_arr_clean, documentation_url)
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
            documentation_url,
            len(link_arr_valid),
            len(documentation_references.references),
        )
    return documentation_references, link_arr_valid


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
    starter_links: Collection[str] = (),
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
        last_iteration: bool - flag indicating the last iteration, whose links are reported but never selected by the LLM
        on_documentation_scraped: optional async callback called immediately after a documentation is scraped and prepared for processing
        starter_links: the links the user picked; their data files are saved even when they are no API description
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

    starters = {link.rstrip("/") for link in starter_links}
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
            if await _save_data_documentation(
                str(url),
                content,
                content_types[str(url)],
                starter=str(url).rstrip("/") in starters,
                spec_required=False,
                saved_documentations=saved_documentations,
                on_documentation_scraped=on_documentation_scraped,
            ):
                data_documentations_count += 1

    if data_fallback_to_html_count:
        logger.debug(
            "[Scrape:Loop] Iteration %s: %s data-file links fell back to HTML scraping",
            curr_iteration,
            data_fallback_to_html_count,
        )

    new_links_to_scrape: List[str] = []
    scraped_documentations_count = 0
    current_scraped_urls = [url.rstrip("/") for url in other_links]
    referenced_spec_urls: List[str] = []
    max_spec_urls_per_page = config.scrape_and_process.max_spec_urls_per_page

    logger.info(
        "[Scrape:Loop] Iteration %s: Starting HTML scrape for %s URLs",
        curr_iteration,
        len(other_links),
    )

    async for scraped_link in scrape_urls(other_links, prune_content=False):
        scraped_documentations_count += 1
        if validate_pydantic_object(scraped_link.url, HttpUrl) and scraped_link.markdown is not None:
            page_url = scraped_link.url.rstrip("/")
            content = await asyncio.to_thread(
                extract_main_content, scraped_link.html, page_url, config.scrape_and_process.main_content_selectors
            )
            content_type = "text/markdown"
            documentation = SavedDocumentation(
                url=page_url,
                content_type=content_type,
                content=content,
                links=[],
                retrieval=RetrievalMetadata(starter=page_url in starters),
            )
            if not _is_usable_content(content):
                # Keep following the page's links (a hub can still lead to documentation), but never save
                # a page without main content (only navigation) or a markdown error message as documentation.
                logger.warning(
                    "[Scrape:Loop] Documentation %s has no usable main content, not saving it",
                    str(scraped_link.url),
                )
            elif on_documentation_scraped:
                await on_documentation_scraped(documentation)

            if max_spec_urls_per_page > 0:
                page_spec_urls = await discover_spec_urls(scraped_link.html, page_url, _page_link_urls(scraped_link))
                if len(page_spec_urls) > max_spec_urls_per_page:
                    logger.warning(
                        "[Scrape:Loop] Documentation %s references %s API descriptions, fetching the first %s "
                        "(SCRAPE_AND_PROCESS__MAX_SPEC_URLS_PER_PAGE)",
                        page_url,
                        len(page_spec_urls),
                        max_spec_urls_per_page,
                    )
                referenced_spec_urls.extend(page_spec_urls[:max_spec_urls_per_page])

            documentation_references = process_citations_markdown(
                markdown_references=scraped_link.markdown.references_markdown,
                text_with_citations=scraped_link.markdown.markdown_with_citations,
                documentation_url=str(scraped_link.url),
            )

            link_arr = [ref.url for ref in documentation_references.references if ref.url]
            logger.info(
                "[Scrape:Loop] Extracted %s raw links from documentation %s",
                len(link_arr),
                str(scraped_link.url),
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

            documentation_references, link_arr_valid = await asyncio.to_thread(
                _clean_documentation_links, documentation_references, link_arr, str(scraped_link.url)
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

            documentation_references_saved = await asyncio.to_thread(
                remove_citations, documentation_references_saved, not_saved_or_current_irrelevant_links
            )

            documentation_references = await asyncio.to_thread(
                remove_citations, documentation_references, new_irrelevant_links
            )

            if len(partly_filtered_new_links) != len(documentation_references.references):
                logger.warning(
                    "[Scrape:Loop] After initial filtering, %s valid links remain but there are %s references for documentation %s",
                    len(partly_filtered_new_links),
                    len(documentation_references.references),
                    str(scraped_link.url),
                )

            if partly_filtered_new_links and last_iteration:
                # Links found on the last iteration are never scraped, so they need no LLM selection. They only
                # tell the caller that unvisited in-scope links remain (finish reason max-iterations-reached).
                new_links_to_scrape.extend(partly_filtered_new_links)
                documentation.links = []
                documentation.documentation_references = documentation_references_saved
                saved_documentations[str(scraped_link.url)] = documentation

            elif partly_filtered_new_links:
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
                        "[Scrape:Loop] LLM identified %s relevant links on documentation %s",
                        len(relevant_links),
                        str(scraped_link.url),
                    )
                    llm_irrelevant_links = list(set(partly_filtered_new_links) - set(relevant_links))
                    new_irrelevant_links.extend(llm_irrelevant_links)
                    # TODO: maybe we should do this only after all documentations are processed
                    irrelevant_links.extend(llm_irrelevant_links)
                    new_links_to_scrape.extend(relevant_links)
                    documentation_references = await asyncio.to_thread(
                        remove_citations, documentation_references, llm_irrelevant_links
                    )
                    documentation_references_saved = await asyncio.to_thread(
                        remove_citations, documentation_references_saved, llm_irrelevant_links
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

    referenced_specs_count = await _save_referenced_specs(
        referenced_spec_urls,
        trusted_domains=trusted_domains,
        saved_documentations=saved_documentations,
        on_documentation_scraped=on_documentation_scraped,
    )
    # A link selected for the next iteration may since have been saved, e.g. as a referenced spec.
    new_links_to_scrape = [link for link in new_links_to_scrape if link not in saved_documentations]

    logger.info(
        "[Scrape:Loop] Iteration %s: Scraped %s HTML documentations successfully, saved %s referenced API descriptions",
        curr_iteration,
        scraped_documentations_count,
        referenced_specs_count,
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
