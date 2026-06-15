# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
from typing import Optional
from uuid import UUID

from src.common.chunk_processor.llms import get_llm_processed_chunk
from src.common.chunk_processor.prompts import get_llm_chunk_process_prompt
from src.common.chunk_processor.schema import ChunkProcessingError, SavedDocumentation
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
) -> tuple[list[DocumentationItem], list[ChunkProcessingError]]:
    """
    Process a single scraped documentation:
    * generate chunks from the content
    * for each chunk, generate summary, tags and category
    * for each chunk, create DocumentationItem object

    Chunks are processed with per-chunk fault isolation: a failure on one chunk (e.g. a
    transient LLM connection error that survived retries) is recorded and skipped instead of
    aborting the whole documentation, so the remaining successful chunks are still returned.

    inputs:
        documentation: SavedDocumentation - the scraped documentation to process
        semaphore: asyncio.Semaphore - semaphore for limiting concurrency
        app: str - application name
        app_version: str - application version
        chunk_length: int - maximum length of each chunk
    outputs:
        (chunks, errors): the successfully built DocumentationItem objects and any per-chunk failures
    """

    logger.debug("[Scrape:Process] Processing documentation: %s", documentation.url)
    chunks = split_text_with_token_overlap(documentation.content, max_tokens=chunk_length, overlap_ratio=0.05)
    logger.debug("[Scrape:Process] Generated %s chunks for documentation: %s", len(chunks), documentation.url)

    async def process_chunk(idx: int, chunk: tuple[str, int]) -> tuple[int, DocumentationItem]:
        prompts = get_llm_chunk_process_prompt(chunk[0], str(documentation.url), app, app_version)

        # A single shared semaphore is applied at chunk level
        async with semaphore:
            data = await get_llm_processed_chunk(prompts)

        documentation_chunk = DocumentationItem(
            doc_id=documentation.id,
            url=str(documentation.url),
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
        return idx, documentation_chunk

    settled = await asyncio.gather(
        *[process_chunk(idx, chunk) for idx, chunk in enumerate(chunks)],
        return_exceptions=True,
    )

    processed_chunks: list[tuple[int, DocumentationItem]] = []
    errors: list[ChunkProcessingError] = []
    for idx, outcome in enumerate(settled):
        if isinstance(outcome, BaseException):
            logger.warning(
                "[Scrape:Process] Skipping chunk %s of documentation %s after failure: %s",
                idx,
                documentation.url,
                outcome,
            )
            errors.append(
                ChunkProcessingError(url=str(documentation.url), chunk_index=idx, error=str(outcome) or repr(outcome))
            )
            continue
        processed_chunks.append(outcome)

    processed_chunks.sort(key=lambda item: item[0])
    documentation_chunks = [item[1] for item in processed_chunks]

    logger.info(
        "[Scrape:Process] Completed processing documentation %s: %s chunks succeeded, %s failed",
        documentation.url,
        len(documentation_chunks),
        len(errors),
    )
    return documentation_chunks, errors


async def process_all_documentations(
    documentations: list[SavedDocumentation],
    app: str,
    app_version: str,
    source: str,
    *,
    semaphore: Optional[asyncio.Semaphore] = None,
    chunk_length: Optional[int] = None,
) -> tuple[list[DocumentationItem], list[ChunkProcessingError]]:
    """
    Process all scraped documentations concurrently with semaphore limiting.

    Per-chunk failures are isolated and returned alongside the successful chunks rather than
    aborting the batch, so a transient LLM error on one chunk cannot discard the whole run.

    inputs:
        documentations: list - list of Saveddocumentation objects to process
        app: str - application name
        app_version: str - application version
        chunk_length: int - maximum length of each chunk
        max_concurrent: int - maximum number of concurrent tasks
    outputs:
        (chunks, errors): all successfully built DocumentationItem objects and any per-chunk failures
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

    all_chunks: list[DocumentationItem] = []
    all_errors: list[ChunkProcessingError] = []
    for documentation_chunks, documentation_errors in results:
        all_chunks.extend(documentation_chunks)
        all_errors.extend(documentation_errors)

    return all_chunks, all_errors
