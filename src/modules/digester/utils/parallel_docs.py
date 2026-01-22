#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

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
    TODO
    """
    total_docs = len(doc_items)
    update_job_progress(job_id, total_processing=total_docs, message="Processing documents")

    async def _process_single_doc(doc_item: dict) -> Tuple[T, List[int], UUID]:
        """Process a single document and return its results."""
        doc_uuid = UUID(doc_item["uuid"])
        doc_content = doc_item["content"]

        logger.info(f"[{logger_scope}] Processing document (UUID: {doc_uuid})")

        result, relevant_indices = await extractor(doc_content, job_id, doc_uuid)

        await increment_processed_documents(job_id, delta=1)
        return result, relevant_indices, doc_uuid

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
    TODO
    """
    update_job_progress(
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

    return await asyncio.gather(*tasks)
