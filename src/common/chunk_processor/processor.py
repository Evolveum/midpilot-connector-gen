# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
from typing import Optional
from uuid import UUID

from src.common.chunk_processor.llms import get_llm_processed_chunk
from src.common.chunk_processor.prompts import get_llm_chunk_process_prompt
from src.common.chunk_processor.schema import SavedDocumentation
from src.common.chunks import split_text_with_token_overlap
from src.common.session.schema import DocumentationItem
from src.config import config

logger = logging.getLogger(__name__)


async def process_scraped_documentation(
    documentation: SavedDocumentation,
    semaphore: asyncio.Semaphore,
    app: str,
    app_version: str,
    chunk_length: int,
    source: str,
    scraper_job_id: Optional[UUID] = None,
) -> list[DocumentationItem]:
    """
    Process a single scraped documentation:
    * generate chunks from the content
    * for each chunk, generate summary, tags and category
    * for each chunk, create DocumentationItem object

    inputs:
        documentation: SavedDocumentation - the scraped documentation to process
        semaphore: asyncio.Semaphore - semaphore for limiting concurrency
        app: str - application name
        app_version: str - application version
        chunk_length: int - maximum length of each chunk
    outputs:
        chunks: list - list of DocumentationItem objects for the documentation
    """

    async with semaphore:
        logger.debug("[Scrape:Process] Processing documentation: %s", documentation.url)
        chunks = split_text_with_token_overlap(documentation.content, max_tokens=chunk_length, overlap_ratio=0.05)
        logger.debug("[Scrape:Process] Generated %s chunks for documentation: %s", len(chunks), documentation.url)
        documentation_chunks = []

        for idx, chunk in enumerate(chunks):
            prompts = get_llm_chunk_process_prompt(chunk[0], str(documentation.url), app, app_version)

            data = await get_llm_processed_chunk(prompts)

            documentation_chunk = DocumentationItem(
                doc_id=documentation.id,
                url=str(documentation.url),
                # chunk_number=idx,
                source=source,
                scrape_job_ids=[scraper_job_id] if scraper_job_id else [],
                summary=data.summary,
                metadata={
                    "category": data.category,
                    "tags": data.tags,
                    "num_endpoints": data.num_endpoints,
                    "length": chunk[1],
                    "contentType": documentation.contentType,
                    "different_app_name": data.different_app_name,
                },
                content=chunk[0],
            )
            documentation_chunks.append(documentation_chunk)

        logger.info(
            "[Scrape:Process] Completed processing documentation %s: generated %s chunks",
            documentation.url,
            len(documentation_chunks),
        )
        return documentation_chunks


async def process_all_documentations(
    documentations: list[SavedDocumentation],
    app: str,
    app_version: str,
    source: str,
    *,
    semaphore: Optional[asyncio.Semaphore] = None,
    chunk_length: Optional[int] = None,
) -> list[DocumentationItem]:
    """
    Process all scraped documentations concurrently with semaphore limiting.

    inputs:
        documentations: list - list of Saveddocumentation objects to process
        app: str - application name
        app_version: str - application version
        chunk_length: int - maximum length of each chunk
        max_concurrent: int - maximum number of concurrent tasks
    outputs:
        list - list of documentationChunk objects for all documentations
    """
    effective_chunk_length = chunk_length or config.scrape_and_process.chunk_length
    local_semaphore = semaphore or asyncio.Semaphore(config.scrape_and_process.max_concurrent)

    results = await asyncio.gather(
        *[
            process_scraped_documentation(
                documentation, local_semaphore, app, app_version, effective_chunk_length, source
            )
            for documentation in documentations
        ]
    )

    all_chunks = []
    for documentation_chunks in results:
        all_chunks.extend(documentation_chunks)

    logger.info(
        "[Scrape:Process] Processing complete: generated %s total chunks from %s documentations",
        len(all_chunks),
        len(documentations),
    )
    return all_chunks
