# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Tuple, TypeVar
from uuid import UUID

from src.common.jobs import increment_processed_documents, update_job_progress
from src.config import config

logger = logging.getLogger(__name__)

T = TypeVar("T")
_digester_llm_semaphore: asyncio.Semaphore | None = None
_digester_llm_semaphore_limit: int | None = None


def _get_digester_llm_limit() -> int:
    return max(1, config.digester.max_concurrent_llm_calls)


def _get_digester_llm_semaphore() -> asyncio.Semaphore:
    global _digester_llm_semaphore, _digester_llm_semaphore_limit

    limit = _get_digester_llm_limit()
    semaphore = _digester_llm_semaphore
    if semaphore is None or _digester_llm_semaphore_limit != limit:
        semaphore = asyncio.Semaphore(limit)
        _digester_llm_semaphore = semaphore
        _digester_llm_semaphore_limit = limit

    return semaphore


async def run_with_digester_llm_limit(callback: Callable[[], Awaitable[T]]) -> T:
    """
    Run digester LLM work behind the process-wide concurrency limit.
    """
    async with _get_digester_llm_semaphore():
        return await callback()


async def invoke_llm(chain: Any, input: Any, **kwargs: Any) -> Any:
    """
    Run one digester LLM chain invocation behind the process-wide digester LLM limit.
    """

    async def _invoke() -> Any:
        return await chain.ainvoke(input, **kwargs)

    return await run_with_digester_llm_limit(_invoke)


async def run_chunks_concurrently(
    *,
    chunk_items: List[dict],
    job_id: UUID,
    extractor: Callable[[str, UUID, UUID], Awaitable[Tuple[T, bool]]],
    logger_scope: str,
) -> List[Tuple[T, bool, UUID]]:
    """
    Process multiple chunks in parallel using the provided extractor function.

    Takes a list of chunk items and processes them concurrently, updating job progress
    and tracking completion. Each chunk is processed using the extractor
    function which returns the result and a relevance flag.

    Args:
        chunk_items: List of chunk dictionaries containing 'chunkId' and 'content' keys
        job_id: UUID for job tracking and progress updates
        extractor: Async function that processes chunk content and returns (result, has_relevant_data)
        logger_scope: String prefix for logging messages

    Returns:
        List of tuples containing (result, has_relevant_data, chunk_id) for each processed chunk
    """
    total_chunks = len(chunk_items)
    await update_job_progress(job_id, total_processing=total_chunks, message="Processing chunks")
    semaphore = asyncio.Semaphore(_get_digester_llm_limit())

    async def _process_single_chunk_item(chunk_item: dict) -> Tuple[T, bool, UUID]:
        """Process a single chunk and return its results."""
        async with semaphore:
            chunk_id = UUID(chunk_item["chunkId"])
            chunk_content = chunk_item["content"]

            result, has_relevant_data = await extractor(chunk_content, job_id, chunk_id)

            await increment_processed_documents(job_id, delta=1)
            return result, has_relevant_data, chunk_id

    return list(await asyncio.gather(*(_process_single_chunk_item(chunk_item) for chunk_item in chunk_items)))


async def run_chunk_groups_concurrently(
    *,
    chunks_by_id: Dict[str, List[str]],
    job_id: UUID,
    extractor: Callable[[UUID, List[str]], Awaitable[Tuple[T, List[Dict[str, Any]]]]],
    logger_scope: str,
    total_groups: int,
) -> List[Tuple[T, List[Dict[str, Any]]]]:
    """
    Process grouped chunks in parallel, with each chunk-id group processed together.

    Takes a dictionary mapping chunk IDs to their respective chunks and processes
    each chunk-id group concurrently using the provided extractor function. Updates
    job progress and tracks completion.

    Args:
        chunks_by_id: Dictionary mapping chunk ID strings to lists of chunk texts
        job_id: UUID for job tracking and progress updates
        extractor: Async function that processes chunk-id groups and returns (result, relevant_chunks)
        logger_scope: String prefix for logging messages
        total_groups: Total number of chunk-id groups for progress tracking

    Returns:
        List of tuples containing (result, relevant_chunks) for each processed chunk-id group
    """
    await update_job_progress(
        job_id,
        total_processing=total_groups,
        processing_completed=0,
        message="Processing selected chunks",
    )
    semaphore = asyncio.Semaphore(_get_digester_llm_limit())

    async def _process_single_chunk(chunk_id: UUID, chunks: List[str]) -> Tuple[T, List[Dict[str, Any]]]:
        async with semaphore:
            result, relevant_chunks = await extractor(chunk_id, chunks)
            await increment_processed_documents(job_id, delta=1)
            return result, relevant_chunks

    tasks = [_process_single_chunk(UUID(chunk_id), chunks) for chunk_id, chunks in chunks_by_id.items()]

    return list(await asyncio.gather(*tasks))
