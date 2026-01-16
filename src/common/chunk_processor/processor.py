#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

import asyncio
import logging

from ...config import config
from ..chunks import split_text_with_token_overlap
from ..session.schema import DocumentationItem
from .llms import get_llm_processed_chunk
from .prompts import get_llm_chunk_process_prompt
from .schema import SavedPage

logger = logging.getLogger(__name__)


async def process_scraped_page(
    page: SavedPage, semaphore: asyncio.Semaphore, app: str, app_version: str, chunk_length: int, source: str
) -> list[DocumentationItem]:
    """
    Process a single scraped page:
    * generate chunks from the content
    * for each chunk, generate summary, tags and category
    * for each chunk, create DocumentationItem object

    inputs:
        page: SavedPage - the scraped page to process
        semaphore: asyncio.Semaphore - semaphore for limiting concurrency
        app: str - application name
        app_version: str - application version
        chunk_length: int - maximum length of each chunk
    outputs:
        chunks: list - list of DocumentationItem objects for the page
    """

    async with semaphore:
        logger.debug("[Scrape:Process] Processing page: %s", page.url)
        chunks = split_text_with_token_overlap(page.content, max_tokens=chunk_length, overlap_ratio=0.05)
        logger.debug("[Scrape:Process] Generated %s chunks for page: %s", len(chunks), page.url)
        page_chunks = []

        for idx, chunk in enumerate(chunks):
            prompts = get_llm_chunk_process_prompt(chunk[0], str(page.url), app, app_version)

            data = await get_llm_processed_chunk(prompts)

            page_chunk = DocumentationItem(
                page_id=page.id,
                url=str(page.url),
                # chunk_number=idx,
                source=source,
                summary=data.summary,
                metadata={
                    "category": data.category,
                    "tags": data.tags,
                    "llm_tags": data.llm_tags,
                    "llm_category": data.llm_category,
                    "num_endpoints": data.num_endpoints,
                    "length": chunk[1],
                    "contentType": page.contentType,
                },
                content=chunk[0],
            )
            page_chunks.append(page_chunk)

        logger.info("[Scrape:Process] Completed processing page %s: generated %s chunks", page.url, len(page_chunks))
        return page_chunks


async def process_all_pages(pages: list[SavedPage], app: str, app_version: str, source: str) -> list[DocumentationItem]:
    """
    Process all scraped pages concurrently with semaphore limiting.

    inputs:
        pages: list - list of SavedPage objects to process
        app: str - application name
        app_version: str - application version
        chunk_length: int - maximum length of each chunk
        max_concurrent: int - maximum number of concurrent tasks
    outputs:
        list - list of PageChunk objects for all pages
    """
    logger.info(
        "[Scrape:ProcessAll] Starting to process %s pages with max %s concurrent tasks",
        len(pages),
        config.scrape_and_process.max_concurrent,
    )
    semaphore = asyncio.Semaphore(config.scrape_and_process.max_concurrent)

    results = await asyncio.gather(
        *[
            process_scraped_page(page, semaphore, app, app_version, config.scrape_and_process.chunk_length, source)
            for page in pages
        ]
    )

    all_chunks = []
    for page_chunks in results:
        all_chunks.extend(page_chunks)

    logger.info(
        "[Scrape:ProcessAll] Processing complete: generated %s total chunks from %s pages", len(all_chunks), len(pages)
    )
    return all_chunks
