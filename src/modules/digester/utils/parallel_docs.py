# Copyright (c) 2025 Evolveum and contributors
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
    extractor: Callable[[str, UUID, UUID], Awaitable[Tuple[T, List[int]]]],
    logger_scope: str,
) -> List[Tuple[T, List[int], UUID]]:
    """
    Generic function to process multiple documents in parallel.

    Each document will be processed by the provided extractor function concurrently.
    Progress tracking is handled automatically.

    Args:
        doc_items: List of document items, each with 'uuid' and 'content' keys
        job_id: Job ID for progress tracking
        extractor: Async function that processes a single document
                   Takes (content, job_id, doc_uuid) and returns (result, relevant_indices)
        logger_scope: Scope prefix for logging (e.g., "Digester:ObjectClasses")

    Returns:
        List of tuples: (result, relevant_indices, doc_uuid) for each document
    """
    total_docs = len(doc_items)

    update_job_progress(job_id, total_documents=total_docs, processed_documents=0, message="Processing documents")

    async def _process_single_doc(doc_item: dict, idx: int) -> Tuple[T, List[int], UUID]:
        """Process a single document and return its results."""
        doc_uuid = UUID(doc_item["uuid"])
        doc_content = doc_item["content"]

        logger.info(f"[{logger_scope}] Processing document {idx}/{total_docs} (UUID: {doc_uuid})")

        result, relevant_indices = await extractor(doc_content, job_id, doc_uuid)

        logger.info(f"[{logger_scope}] Document {doc_uuid}: completed with {len(relevant_indices)} relevant chunks")

        # Mark this document as complete
        increment_processed_documents(job_id, delta=1)

        return result, relevant_indices, doc_uuid

    # Process all documents in parallel
    results = await asyncio.gather(*(_process_single_doc(doc_item, idx) for idx, doc_item in enumerate(doc_items, 1)))

    return results


async def process_grouped_chunks_in_parallel(
    *,
    doc_to_chunks: Dict[str, List[Tuple[int, int, str]]],
    job_id: UUID,
    extractor: Callable[[UUID, List[Tuple[int, int, str]], int], Awaitable[Tuple[T, List[Dict[str, Any]]]]],
    logger_scope: str,
    total_documents: int,
) -> List[Tuple[T, List[Dict[str, Any]]]]:
    """
    Generic function to process grouped chunks from multiple documents in parallel.

    This is useful when you have pre-selected chunks grouped by document UUID
    (e.g., for attributes and endpoints extraction).

    Args:
        doc_to_chunks: Dictionary mapping doc_uuid to list of (array_idx, original_idx, chunk_text) tuples
        job_id: Job ID for progress tracking
        extractor: Async function that processes chunks from a single document
                   Takes (doc_uuid, doc_chunks, doc_index) and returns (result, relevant_chunks)
        logger_scope: Scope prefix for logging (e.g., "Digester:Attributes")
        total_documents: Total number of documents being processed

    Returns:
        List of tuples: (result, relevant_chunks) for each document
    """
    update_job_progress(
        job_id,
        total_documents=total_documents,
        processed_documents=0,
        message="Processing selected chunks",
    )

    async def _process_single_document(
        doc_uuid: UUID, doc_chunks: List[Tuple[int, int, str]], doc_index: int
    ) -> Tuple[T, List[Dict[str, Any]]]:
        """Process chunks from a single document and return results."""
        num_chunks = len(doc_chunks)
        logger.info(
            f"[{logger_scope}] Processing document {doc_index}/{total_documents} (UUID: {doc_uuid}, {num_chunks} chunks)"
        )

        result, relevant_chunks = await extractor(doc_uuid, doc_chunks, doc_index)

        increment_processed_documents(job_id, delta=1)

        return result, relevant_chunks

    # Process all documents in parallel
    tasks = [
        _process_single_document(UUID(doc_uuid), doc_chunks, doc_index)
        for doc_index, (doc_uuid, doc_chunks) in enumerate(doc_to_chunks.items(), start=1)
    ]
    results = await asyncio.gather(*tasks)

    return results
