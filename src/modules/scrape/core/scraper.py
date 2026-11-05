# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""One iteration of the scraper: fetch, assess and save documents, then choose the links of the next one.

Every text document is assessed before it is saved: a page is saved when the assessment keeps it and finds
no evidence that it documents another product; starter links are always saved, and such a mismatch on one is
recorded as a job error. A version mismatch is only recorded in the page's retrieval metadata. API descriptions are validated deterministically instead. The links of all pages
of an iteration are collected, ranked within the crawl scope and chosen by the link selection in batches.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import HttpUrl

from src.config import config
from src.core.concurrency import TaskScope
from src.core.errors import LLMUnavailableError
from src.core.schema import validate_pydantic_object
from src.documents import RetrievalMetadata, SavedDocumentation, SpecRetrievalMetadata
from src.documents.spec_validation import SpecDescription, describe_spec, parse_structured_document
from src.integrations.web import get_all_content_types, scrape_all_data_documentations, scrape_urls
from src.integrations.web.page_content import extract_main_content
from src.integrations.web.page_links import PageLink, page_links
from src.integrations.web.spec_discovery import discover_spec_urls
from src.integrations.web.urls import is_forbidden_url
from src.modules.scrape.core.assessment import assess_page, select_links
from src.modules.scrape.core.candidates import CrawlScope, rank_candidate_links

logger = logging.getLogger(__name__)


# crawl4ai reports markdown generation failures inside the markdown string itself.
_CRAWL4AI_MARKDOWN_ERROR_PREFIXES = ("Error converting HTML to markdown", "Error generating fit markdown")
_DATA_CONTENT_TYPE_MARKERS = ("json", "yaml", "yml", "text/plain")

DocumentationCallback = Callable[[SavedDocumentation], Awaitable[None]]
ErrorReporter = Callable[[str], Awaitable[None]]


@dataclass
class ScrapeRun:
    """What the iterations of one scrape job share: its target, scope and everything saved or seen so far.

    ``version`` is the concrete application version the scrape targets, or None when it targets any version.
    ``report_error`` records a non-fatal error on the job.
    """

    app: str
    version: str | None
    scope: CrawlScope
    starter_links: Collection[str] = ()
    forbidden_url_parts: list[str] = field(default_factory=list)
    on_documentation_scraped: DocumentationCallback | None = None
    report_error: ErrorReporter | None = None
    saved_documentations: dict[str, SavedDocumentation] = field(default_factory=dict)
    seen_links: set[str] = field(default_factory=set)
    llm_slots: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(config.scrape_and_process.max_concurrent_page_assessments)
    )

    def __post_init__(self) -> None:
        self._starters = {link.rstrip("/") for link in self.starter_links}

    def is_starter(self, url: str) -> bool:
        return url.rstrip("/") in self._starters

    async def save(self, documentation: SavedDocumentation) -> None:
        self.saved_documentations[documentation.url] = documentation
        if self.on_documentation_scraped:
            await self.on_documentation_scraped(documentation)

    async def error(self, message: str) -> None:
        if self.report_error:
            await self.report_error(message)


@dataclass(frozen=True)
class _TextDocument:
    """A fetched page (its main content) or plain-text data file, waiting for its assessment."""

    url: str
    content: str
    content_type: str
    title: str = ""
    links: tuple[PageLink, ...] = ()
    spec_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Outcome:
    document: _TextDocument
    follow_links: bool


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


def _saved_copy_of(spec: SpecDescription, saved_documentations: dict[str, SavedDocumentation]) -> str | None:
    """URL of an already saved document that is the same API description, if any."""
    for saved_url, saved in saved_documentations.items():
        saved_spec = saved.retrieval.spec if saved.retrieval else None
        if saved_spec is not None and saved_spec.canonical_hash == spec.canonical_hash:
            return saved_url
    return None


async def _save_structured_data_file(
    url: str, content: str, content_type: str, spec: SpecDescription | None, *, run: ScrapeRun, depth: int
) -> bool:
    """Save a JSON/YAML data file when it is worth keeping; return whether it was saved.

    API descriptions are kept unless this job already saved the same one (another format or another server
    list of it). Other JSON/YAML files (``package.json``, translation bundles) are kept only as starter links
    the user picked, which are always kept.
    """
    starter = run.is_starter(url)
    if spec is None and not starter:
        logger.info("[Scrape:Loop] Data documentation %s is not an API description, not saving it", url)
        return False
    if spec is not None and not starter and (copy_url := _saved_copy_of(spec, run.saved_documentations)) is not None:
        logger.info("[Scrape:Loop] %s is the same API description as %s, not saving it", url, copy_url)
        return False
    if spec is not None:
        logger.info(
            "[Scrape:Loop] Saving %s document %s (title %r, version %r)", spec.format, url, spec.title, spec.version
        )
    await run.save(
        SavedDocumentation(
            url=url,
            content_type=content_type,
            content=content,
            retrieval=RetrievalMetadata(
                starter=starter,
                depth=depth,
                spec=SpecRetrievalMetadata.from_description(spec) if spec is not None else None,
            ),
        )
    )
    return True


async def _save_referenced_specs(spec_urls: list[str], *, run: ScrapeRun, depth: int) -> int:
    """Fetch the API descriptions the scraped pages reference (one hop, never crawled further).

    Only specs in the crawl scope are fetched, plus raw GitHub files: vendors often publish the spec in
    their repository and link it from their documentation. Only a recognized API description is saved.
    Returns the number of saved specs.
    """
    candidates = [url for url in dict.fromkeys(spec_urls) if url not in run.saved_documentations]
    in_scope = [url for url in candidates if run.scope.allows_api_description(url)]
    if len(in_scope) != len(candidates):
        logger.info(
            "[Scrape:Loop] Skipping %s referenced API descriptions outside the crawl scope",
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
        if not _is_usable_content(content):
            logger.warning("[Scrape:Loop] Data documentation %s is empty, not saving it", url)
            continue
        _, spec = await asyncio.to_thread(_inspect_data_documentation, content)
        if spec is None:
            logger.info("[Scrape:Loop] Data documentation %s is not an API description, not saving it", url)
            continue
        if await _save_structured_data_file(url, content, content_types.get(url, ""), spec, run=run, depth=depth):
            saved_count += 1
    return saved_count


def _page_title(result: Any) -> str:
    metadata = getattr(result, "metadata", None)
    title = metadata.get("title") if isinstance(metadata, dict) else None
    return title.strip() if isinstance(title, str) else ""


async def _assess_and_save(document: _TextDocument, *, run: ScrapeRun, depth: int) -> _Outcome:
    """Assess a text document, save it when it is documentation, and tell whether to follow its links."""
    if not _is_usable_content(document.content):
        # Keep following the page's links (a hub can still lead to documentation), but never save a page
        # without main content (only navigation) or a markdown error message as documentation.
        logger.warning("[Scrape:Loop] Documentation %s has no usable main content, not saving it", document.url)
        return _Outcome(document, follow_links=True)

    starter = run.is_starter(document.url)
    keep, assessment = True, None
    try:
        async with run.llm_slots:
            keep, assessment = await assess_page(
                url=document.url, title=document.title, content=document.content, app=run.app, version=run.version
            )
    except LLMUnavailableError:
        raise
    except Exception as exc:
        logger.exception("[Scrape:Assess] Assessment of %s failed, saving it unassessed", document.url)
        await run.error(f"Page assessment failed for {document.url}; saved without assessment: {exc}")

    other_product = assessment is not None and assessment.product_match == "no"
    other_version = assessment is not None and assessment.version_match == "no"
    if starter and assessment is not None and other_product:
        logger.warning("[Scrape:Assess] Starter link %s documents another product", document.url)
        await run.error(
            f"Starter link {document.url} documents another product ({assessment.product_evidence!r}); it is "
            "saved because it is a starter link, and its links are not followed."
        )
    elif starter and assessment is not None and other_version:
        logger.warning("[Scrape:Assess] Starter link %s documents another version than %s", document.url, run.version)
        await run.error(
            f"Starter link {document.url} documents another version than {run.version} "
            f"({assessment.version_evidence!r}); it is saved and its links are followed."
        )
    if starter or (keep and not other_product):
        if other_version and not starter:
            # A vendor often publishes only its latest documentation, the best there is for an older version,
            # so a version mismatch is recorded for the digester instead of discarding the page.
            logger.info("[Scrape:Assess] Saving %s, which documents another version", document.url)
        await run.save(
            SavedDocumentation(
                url=document.url,
                content_type=document.content_type,
                content=document.content,
                retrieval=RetrievalMetadata(starter=starter, depth=depth, assessment=assessment),
            )
        )
    else:
        logger.info(
            "[Scrape:Assess] Not saving %s: %s",
            document.url,
            "another product"
            if other_product
            else f"no documentation for the connector (role {assessment.role if assessment else 'unknown'})",
        )
    return _Outcome(document, follow_links=not other_product)


def _followed_links(outcomes: Sequence[_Outcome]) -> list[PageLink]:
    """Links of the pages whose links are followed, without the pages that have too many of them."""
    max_links = config.scrape_and_process.max_links_per_documentation
    links: list[PageLink] = []
    for outcome in outcomes:
        if not outcome.follow_links:
            continue
        page_links_found = outcome.document.links
        if max_links > 0 and len(page_links_found) > max_links:
            logger.warning(
                "[Scrape:Loop] Documentation %s has %s links, more than %s "
                "(SCRAPE_AND_PROCESS__MAX_LINKS_PER_DOCUMENTATION); not following them",
                outcome.document.url,
                len(page_links_found),
                max_links,
            )
            continue
        links.extend(page_links_found)
    return links


def _candidate_links(links: Sequence[PageLink], run: ScrapeRun) -> list[PageLink]:
    """New in-scope links not blocked by the forbidden URL parts, deduplicated and ranked."""
    unique: dict[str, PageLink] = {}
    for link in links:
        if link.url in run.seen_links or link.url in run.saved_documentations or link.url in unique:
            continue
        if is_forbidden_url(link.url, run.forbidden_url_parts):
            continue
        unique[link.url] = link
    return rank_candidate_links(unique.values(), run.scope)


async def _select_next_links(candidates: list[PageLink], run: ScrapeRun) -> list[PageLink]:
    """Links chosen by the link selection, which reads the candidates in batches of ``max_links_per_assessment``."""
    batch_size = config.scrape_and_process.max_links_per_assessment
    batches = [candidates[start : start + batch_size] for start in range(0, len(candidates), batch_size)]

    async def select(batch: list[PageLink]) -> list[PageLink]:
        async with run.llm_slots:
            return await select_links(batch, app=run.app, version=run.version)

    results = await asyncio.gather(*(select(batch) for batch in batches), return_exceptions=True)
    selected: list[PageLink] = []
    for batch, result in zip(batches, results):
        if isinstance(result, BaseException):
            if isinstance(result, LLMUnavailableError) or not isinstance(result, Exception):
                raise result
            logger.error("[Scrape:Links] Link selection failed for %s links: %s", len(batch), result)
            await run.error(f"Link selection failed for {len(batch)} links found on the scraped pages: {result}")
            continue
        selected.extend(result)
    return selected


async def scraper_loop(
    links_to_scrape: list[str], run: ScrapeRun, *, curr_iteration: int = 1, last_iteration: bool = False
) -> list[str]:
    """Scrape one iteration of links and return the links of the next iteration.

    Saved documents end up in ``run.saved_documentations``. Links found on the last iteration are never
    scraped, so they are not selected by the LLM; they only tell the caller that in-scope links remain.
    """
    depth = curr_iteration - 1
    logger.info("[Scrape:Loop] Iteration %s: Starting with %s candidate links", curr_iteration, len(links_to_scrape))
    allowed_links = [
        link for link in links_to_scrape if run.is_starter(link) or not is_forbidden_url(link, run.forbidden_url_parts)
    ]
    if len(allowed_links) != len(links_to_scrape):
        logger.info(
            "[Scrape:Loop] Iteration %s: Skipped %s candidate links matching forbidden URL parts",
            curr_iteration,
            len(links_to_scrape) - len(allowed_links),
        )
    if not allowed_links:
        logger.info("[Scrape:Loop] Iteration %s: No candidate links left after forbidden URL filtering", curr_iteration)
        return []
    run.seen_links.update(link.rstrip("/") for link in allowed_links)

    content_types = await get_all_content_types(allowed_links)
    data_links = [
        url for url, content_type in content_types.items() if any(m in content_type for m in _DATA_CONTENT_TYPE_MARKERS)
    ]
    page_urls = [url for url in content_types if url not in data_links]
    logger.info(
        "[Scrape:Loop] Iteration %s: Link type split -> %s data-file links, %s HTML links",
        curr_iteration,
        len(data_links),
        len(page_urls),
    )

    text_documents: list[_TextDocument] = []
    data_documentations_count = 0
    for url, content in await scrape_all_data_documentations(data_links):
        if not validate_pydantic_object(url, HttpUrl):
            continue
        if content == "error":
            page_urls.append(url)  # Not served as a data file after all: render it as a page.
            continue
        if not _is_usable_content(content):
            logger.warning("[Scrape:Loop] Data documentation %s is empty, not saving it", url)
            continue
        structured, spec = await asyncio.to_thread(_inspect_data_documentation, content)
        if structured or spec is not None:
            if await _save_structured_data_file(url, content, content_types[url], spec, run=run, depth=depth):
                data_documentations_count += 1
        else:
            # Plain text, such as a raw markdown file, is assessed like a page.
            text_documents.append(_TextDocument(url=url, content=content, content_type=content_types[url]))

    logger.info("[Scrape:Loop] Iteration %s: Starting HTML scrape for %s URLs", curr_iteration, len(page_urls))
    max_spec_urls_per_page = config.scrape_and_process.max_spec_urls_per_page
    scraped_pages_count = 0
    async with TaskScope(f"scrape-page-assessment:{curr_iteration}") as assessments:
        tasks = [assessments.start(_assess_and_save(document, run=run, depth=depth)) for document in text_documents]
        async for scraped in scrape_urls(page_urls, prune_content=False):
            if not validate_pydantic_object(scraped.url, HttpUrl):
                continue
            scraped_pages_count += 1
            page_url = scraped.url.rstrip("/")
            content = await asyncio.to_thread(
                extract_main_content, scraped.html, page_url, config.scrape_and_process.main_content_selectors
            )
            links = await asyncio.to_thread(page_links, getattr(scraped, "links", None), page_url)
            spec_urls: list[str] = []
            if max_spec_urls_per_page > 0:
                spec_urls = await discover_spec_urls(scraped.html, page_url, [link.url for link in links])
                if len(spec_urls) > max_spec_urls_per_page:
                    logger.warning(
                        "[Scrape:Loop] Documentation %s references %s API descriptions, fetching the first %s "
                        "(SCRAPE_AND_PROCESS__MAX_SPEC_URLS_PER_PAGE)",
                        page_url,
                        len(spec_urls),
                        max_spec_urls_per_page,
                    )
            document = _TextDocument(
                url=page_url,
                content=content,
                content_type="text/markdown",
                title=_page_title(scraped),
                links=tuple(links),
                spec_urls=tuple(spec_urls[:max_spec_urls_per_page]),
            )
            logger.info("[Scrape:Loop] Extracted %s links from documentation %s", len(links), page_url)
            tasks.append(assessments.start(_assess_and_save(document, run=run, depth=depth)))
        outcomes = await asyncio.gather(*tasks)

    followed = [outcome for outcome in outcomes if outcome.follow_links]
    referenced_specs_count = await _save_referenced_specs(
        [url for outcome in followed for url in outcome.document.spec_urls], run=run, depth=depth + 1
    )
    candidates = _candidate_links(_followed_links(outcomes), run)
    saved_pages_count = sum(1 for outcome in outcomes if outcome.document.url in run.saved_documentations)
    logger.info(
        "[Scrape:Loop] Iteration %s: Scraped %s pages, saved %s of %s assessed documents, %s data files and "
        "%s referenced API descriptions; %s new candidate links",
        curr_iteration,
        scraped_pages_count,
        saved_pages_count,
        len(outcomes),
        data_documentations_count,
        referenced_specs_count,
        len(candidates),
    )
    if not candidates or last_iteration:
        return [link.url for link in candidates]

    selected = await _select_next_links(candidates, run)
    run.seen_links.update(link.url for link in candidates)
    next_links = [link.url for link in selected]
    logger.info(
        "[Scrape:Loop] Iteration %s complete: %s of %s candidate links selected for the next iteration",
        curr_iteration,
        len(next_links),
        len(candidates),
    )
    return next_links
