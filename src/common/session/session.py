# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
import uuid
from typing import Any, Dict, List
from uuid import UUID

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.chunk_processor.llms import get_llm_processed_chunk
from src.common.chunk_processor.prompts import get_llm_chunk_process_prompt
from src.common.database.config import async_session_maker
from src.common.database.repositories.documentation_repository import DocumentationRepository
from src.common.database.repositories.job_repository import JobRepository
from src.common.database.repositories.session_repository import SessionRepository
from src.common.enums import JobStage
from src.common.session.utils.documentation_upload import read_uploaded_documentation
from src.config import config

logger = logging.getLogger(__name__)

_UPLOAD_WORKER_LIMIT = max(1, min(config.database.pool_size, 8))
_UPLOAD_WORKER_SEMAPHORE = asyncio.Semaphore(_UPLOAD_WORKER_LIMIT)


def build_processed_chunk_metadata(
    *,
    filename: str,
    chunk_number: int,
    token_count: int,
    character_count: int,
    num_endpoints: int,
    tags: list[str],
    category: str,
    upload_metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "filename": filename,
        "chunk_number": chunk_number,
        "token_count": token_count,
        "character_count": character_count,
        "num_endpoints": num_endpoints,
        "tags": tags,
        "category": category,
    }
    if upload_metadata:
        metadata.update(upload_metadata)
    return metadata


# Helper Functions
async def process_documentation_worker(
    session_id: UUID,
    chunks: List[tuple[str, int]],
    filename: str,
    doc_id: UUID,
    app: str,
    app_version: str,
    job_id: UUID,
    upload_metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    async with _UPLOAD_WORKER_SEMAPHORE:
        semaphore = asyncio.Semaphore(config.scrape_and_process.max_concurrent)

        async with async_session_maker() as db_init:
            job_repo = JobRepository(db_init)
            await job_repo.update_job_progress(
                job_id,
                stage=JobStage.processing,
                message=f"Processing {len(chunks)} chunks",
                total_processing=len(chunks),
                processing_completed=0,
            )
            await db_init.commit()

        logger.info(
            "[Upload:Job] Processing %s chunks for session %s (job %s) [worker_limit=%s]",
            len(chunks),
            session_id,
            job_id,
            _UPLOAD_WORKER_LIMIT,
        )

        async def process_chunk(idx: int, chunk_data: tuple[str, int]) -> Dict[str, Any]:
            chunk_text, chunk_length = chunk_data

            async with semaphore:
                prompts = get_llm_chunk_process_prompt(chunk_text, filename, app, app_version)
                data = await get_llm_processed_chunk(prompts)

            metadata = build_processed_chunk_metadata(
                filename=filename,
                chunk_number=idx,
                token_count=chunk_length,
                character_count=len(chunk_text),
                num_endpoints=data.num_endpoints,
                tags=data.tags,
                category=data.category,
                upload_metadata=upload_metadata,
            )

            return {
                "idx": idx,
                "chunk_text": chunk_text,
                "summary": data.summary,
                "metadata": metadata,
            }

        processed_chunks = await asyncio.gather(*[process_chunk(i, ch) for i, ch in enumerate(chunks)])

        async with async_session_maker() as db_persist:
            doc_repo = DocumentationRepository(db_persist)
            job_repo = JobRepository(db_persist)

            for item in processed_chunks:
                await doc_repo.create_documentation_item(
                    session_id=session_id,
                    source="upload",
                    content=item["chunk_text"],
                    doc_id=doc_id,
                    original_job_id=job_id,
                    url=f"upload://{filename}",
                    summary=item["summary"],
                    metadata=item["metadata"],
                )
                await job_repo.increment_processed_documents(job_id, 1)
            await db_persist.commit()

        logger.info(
            "[Upload:Job] Completed processing for session %s (job %s): generated %s chunks",
            session_id,
            job_id,
            len(processed_chunks),
        )

        return {
            "chunks_processed": len(processed_chunks),
            "doc_id": doc_id,
            "filename": filename,
        }


async def get_session_documentation(
    session_id: UUID, documentation: UploadFile | None = None, db: AsyncSession | None = None
) -> list[dict]:
    """
    Helper to get all documentation items from session or uploaded file.
    Can be imported by other module routers.
    Returns list of documentation items with their UUIDs and content.
    """
    if db is None:
        async with async_session_maker() as session:
            return await _get_session_documentation_impl(session_id, documentation, session)
    else:
        return await _get_session_documentation_impl(session_id, documentation, db)


async def _get_session_documentation_impl(
    session_id: UUID, documentation: UploadFile | None, db: AsyncSession
) -> list[dict]:
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    doc_repo = DocumentationRepository(db)

    if documentation is not None:
        uploaded = await read_uploaded_documentation(documentation)

        doc_id = uuid.uuid4()
        chunk_id = await doc_repo.create_documentation_item(
            session_id=session_id,
            source="upload",
            content=uploaded.text,
            doc_id=doc_id,
            url=None,
            summary=None,
            metadata=uploaded.metadata,
        )

        await db.commit()
        return [
            {
                "chunkId": str(chunk_id),
                "docId": str(doc_id),
                "source": "upload",
                "scrapeJobIds": [],
                "url": None,
                "summary": None,
                "content": uploaded.text,
                "@metadata": uploaded.metadata,
            }
        ]

    doc_items = await doc_repo.get_documentation_items_by_session(session_id)
    if doc_items:
        return [
            {
                "chunkId": item.get("chunkId"),
                "docId": item.get("docId"),
                "source": item.get("source"),
                "scrapeJobIds": item.get("scrapeJobIds", []),
                "url": item.get("url"),
                "summary": item.get("summary"),
                "content": item.get("content", ""),
                "@metadata": item.get("metadata", {}) or {},
            }
            for item in doc_items
        ]

    raise HTTPException(
        status_code=400,
        detail=f"Session {session_id} has no stored documentation. Please upload documentation file or run scraper.",
    )


async def resolve_session_job_id(
    repo: SessionRepository,
    session_id: UUID,
    job_id: UUID | None,
    session_key: str,
    job_label: str,
    not_found_detail: str | None = None,
) -> UUID:
    if job_id:
        return job_id

    job_id_value = await repo.get_session_data(session_id, session_key)
    if not job_id_value:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=not_found_detail or f"No {job_label} job found in session {session_id}",
        )

    return job_id_value if isinstance(job_id_value, UUID) else UUID(str(job_id_value))


async def ensure_session_exists(repo: SessionRepository, session_id: UUID) -> None:
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")
