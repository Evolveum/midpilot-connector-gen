# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Tuple, TypeVar
from uuid import UUID

from ....common.jobs import increment_processed_documents, update_job_progress

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def process_documents_in_parallel(
    *,
    doc_items: List[dict],
    job_id: UUID,
    extractor: Callable[[str, UUID, UUID], Awaitable[Tuple[T, bool]]],
    logger_scope: str,
) -> List[Tuple[T, bool, UUID]]:
    """
    Process multiple documents in parallel using the provided extractor function.

    Takes a list of document items and processes them concurrently, updating job progress
    and tracking document completion. Each document is processed using the extractor
    function which returns the result and a relevance flag.

    Args:
        doc_items: List of document dictionaries containing 'uuid' and 'content' keys
        job_id: UUID for job tracking and progress updates
        extractor: Async function that processes document content and returns (result, has_relevant_data)
        logger_scope: String prefix for logging messages

    Returns:
        List of tuples containing (result, has_relevant_data, doc_uuid) for each processed document
    """
    total_docs = len(doc_items)
    await update_job_progress(job_id, total_processing=total_docs, message="Processing documents")

    async def _process_single_doc(doc_item: dict) -> Tuple[T, bool, UUID]:
        """Process a single document and return its results."""
        doc_uuid = UUID(doc_item["uuid"])
        doc_content = doc_item["content"]

        logger.info(f"[{logger_scope}] Processing document (UUID: {doc_uuid})")

        result, has_relevant_data = await extractor(doc_content, job_id, doc_uuid)

        await increment_processed_documents(job_id, delta=1)
        return result, has_relevant_data, doc_uuid

    return list(await asyncio.gather(*(_process_single_doc(doc_item) for doc_item in doc_items)))


async def process_grouped_chunks_in_parallel(
    *,
    doc_to_chunks: Dict[str, List[str]],
    job_id: UUID,
    extractor: Callable[[UUID, List[str]], Awaitable[Tuple[T, List[Dict[str, Any]]]]],
    logger_scope: str,
    total_documents: int,
) -> List[Tuple[T, List[Dict[str, Any]]]]:
    """
    Process grouped chunks in parallel, with each document's chunks processed together.

    Takes a dictionary mapping document UUIDs to their respective chunks and processes
    each document's chunks concurrently using the provided extractor function. Updates
    job progress and tracks document completion.

    Args:
        doc_to_chunks: Dictionary mapping document UUID strings to lists of chunk texts
        job_id: UUID for job tracking and progress updates
        extractor: Async function that processes document chunks and returns (result, relevant_chunks)
        logger_scope: String prefix for logging messages
        total_documents: Total number of documents for progress tracking

    Returns:
        List of tuples containing (result, relevant_chunks) for each processed document
    """
    await update_job_progress(
        job_id,
        total_processing=total_documents,
        processing_completed=0,
        message="Processing selected chunks",
    )

    async def _process_single_document(doc_uuid: UUID, chunks: List[str]) -> Tuple[T, List[Dict[str, Any]]]:
        logger.info(f"[{logger_scope}] Processing document (UUID: {doc_uuid})")

        result, relevant_chunks = await extractor(doc_uuid, chunks)
        await increment_processed_documents(job_id, delta=1)
        return result, relevant_chunks

    tasks = [_process_single_document(UUID(doc_uuid), chunks) for doc_uuid, chunks in doc_to_chunks.items()]

    return list(await asyncio.gather(*tasks))
