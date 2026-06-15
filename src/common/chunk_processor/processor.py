# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from src.common.chunk_processor.llms import get_llm_processed_chunk
from src.common.chunk_processor.prompts import get_llm_chunk_process_prompt
from src.common.chunk_processor.schema import ChunkProcessingError, LlmChunkOutput, SavedDocumentation
from src.common.chunks import split_text_with_token_overlap
from src.common.session.schema import DocumentationItem
from src.config import config

logger = logging.getLogger(__name__)


def build_chunk_metadata(
    *,
    chunk_number: int,
    token_count: int,
    character_count: int,
    data: LlmChunkOutput,
    content_type: Optional[str] = None,
    filename: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the metadata dict persisted on a processed documentation chunk.

    Shared by the scraper and upload pipelines so both produce an identical
    metadata shape (notably ``chunk_number``, which was previously only set on
    uploads). ``content_type`` and ``filename`` are optional because the scraper
    carries a content type but no filename, while uploads carry both. ``extra``
    (e.g. upload parser metadata) is merged last.
    """
    metadata: Dict[str, Any] = {
        "chunk_number": chunk_number,
        "token_count": token_count,
        "character_count": character_count,
        "num_endpoints": data.num_endpoints,
        "tags": data.tags,
        "category": data.category,
        "different_app_name": data.different_app_name,
    }
    if content_type is not None:
        metadata["content_type"] = content_type
    if filename is not None:
        metadata["filename"] = filename
    if extra:
        metadata.update(extra)
    return metadata


async def process_scraped_documentation(
    documentation: SavedDocumentation,
    semaphore: asyncio.Semaphore,
    app: str,
    app_version: str,
    chunk_length: int,
    source: str,
    scraper_job_id: Optional[UUID] = None,
) -> Tuple[List[DocumentationItem], List[ChunkProcessingError]]:
    """
    Process a single scraped documentation:
    * generate chunks from the content
    * for each chunk, generate summary, tags and category
    * for each chunk, create DocumentationItem object

    Chunk failures are isolated: a single failing chunk does not discard the other
    chunks of the same documentation, and the failure is returned as a
    ChunkProcessingError so the caller can surface it.

    inputs:
        documentation: SavedDocumentation - the scraped documentation to process
        semaphore: asyncio.Semaphore - semaphore for limiting concurrency
        app: str - application name
        app_version: str - application version
        chunk_length: int - maximum length of each chunk
    outputs:
        tuple of (list of DocumentationItem objects, list of ChunkProcessingError)
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
            metadata=build_chunk_metadata(
                chunk_number=idx,
                token_count=chunk[1],
                character_count=len(chunk[0]),
                data=data,
                content_type=documentation.contentType,
            ),
            content=chunk[0],
        )
        return idx, documentation_chunk

    results = await asyncio.gather(
        *[process_chunk(idx, chunk) for idx, chunk in enumerate(chunks)],
        return_exceptions=True,
    )

    succeeded: list[tuple[int, DocumentationItem]] = []
    errors: List[ChunkProcessingError] = []
    for idx, result in enumerate(results):
        if isinstance(result, BaseException):
            logger.warning(
                "[Scrape:Process] Chunk %s of documentation %s failed: %s",
                idx,
                documentation.url,
                result,
            )
            errors.append(ChunkProcessingError(url=str(documentation.url), chunk_number=idx, error=str(result)))
        else:
            succeeded.append(result)

    succeeded.sort(key=lambda item: item[0])
    documentation_chunks = [item[1] for item in succeeded]

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
) -> Tuple[List[DocumentationItem], List[ChunkProcessingError]]:
    """
    Process all scraped documentations concurrently with semaphore limiting.

    inputs:
        documentations: list - list of Saveddocumentation objects to process
        app: str - application name
        app_version: str - application version
        chunk_length: int - maximum length of each chunk
        max_concurrent: int - maximum number of concurrent tasks
    outputs:
        tuple of (list of DocumentationItem objects, list of ChunkProcessingError)
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

    all_chunks: List[DocumentationItem] = []
    all_errors: List[ChunkProcessingError] = []
    for documentation_chunks, documentation_errors in results:
        all_chunks.extend(documentation_chunks)
        all_errors.extend(documentation_errors)

    return all_chunks, all_errors
