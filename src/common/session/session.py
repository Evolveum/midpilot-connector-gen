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

from ...config import config
from ..chunk_processor.llms import get_llm_processed_chunk
from ..chunk_processor.prompts import get_llm_chunk_process_prompt
from ..database.config import async_session_maker
from ..database.repositories.documentation_repository import DocumentationRepository
from ..database.repositories.job_repository import JobRepository
from ..database.repositories.session_repository import SessionRepository
from ..enums import JobStage
from .schema import DocumentationItem

logger = logging.getLogger(__name__)

_UPLOAD_WORKER_LIMIT = max(1, min(config.database.pool_size, 8))
_UPLOAD_WORKER_SEMAPHORE = asyncio.Semaphore(_UPLOAD_WORKER_LIMIT)


# Helper Functions
async def process_documentation_worker(
    session_id: UUID,
    chunks: List[tuple[str, int]],
    filename: str,
    doc_id: UUID,
    app: str,
    app_version: str,
    job_id: UUID,
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

            metadata = {
                "filename": filename,
                "chunk_number": idx,
                "length": chunk_length,
                "num_endpoints": data.num_endpoints,
                "tags": data.tags,
                "category": data.category,
                "llm_tags": data.tags,
                "llm_category": data.category,
            }

            return {
                "idx": idx,
                "chunk_text": chunk_text,
                "summary": data.summary,
                "metadata": metadata,
            }

        processed_chunks = await asyncio.gather(*[process_chunk(i, ch) for i, ch in enumerate(chunks)])

        doc_items: List[DocumentationItem] = []
        async with async_session_maker() as db_persist:
            doc_repo = DocumentationRepository(db_persist)
            job_repo = JobRepository(db_persist)
            session_repo = SessionRepository(db_persist)

            existing_docs = await session_repo.get_session_data(session_id, "documentationItems") or []

            for item in processed_chunks:
                chunk_id = await doc_repo.create_documentation_item(
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

                doc_item = DocumentationItem(
                    chunk_id=chunk_id,
                    source="upload",
                    doc_id=doc_id,
                    scrape_job_ids=[job_id],
                    url=f"upload://{filename}",
                    summary=item["summary"],
                    content=item["chunk_text"],
                    metadata=item["metadata"],
                )
                doc_items.append(doc_item)
                existing_docs.append(doc_item.model_dump(by_alias=True, mode="json"))

            await session_repo.update_session(session_id, {"documentationItems": existing_docs})
            await db_persist.commit()

        logger.info(
            "[Upload:Job] Completed processing for session %s (job %s): generated %s chunks",
            session_id,
            job_id,
            len(doc_items),
        )

        return {
            "chunks_processed": len(doc_items),
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

    if documentation is not None:
        doc_text = (await documentation.read()).decode("utf-8", errors="ignore")

        doc_repo = DocumentationRepository(db)
        doc_id = uuid.uuid4()
        chunk_id = await doc_repo.create_documentation_item(
            session_id=session_id,
            source="upload",
            content=doc_text,
            doc_id=doc_id,
            url=None,
            summary=None,
            metadata={"filename": documentation.filename or "unknown", "length": len(doc_text)},
        )

        existing_docs: list[dict] = await repo.get_session_data(session_id, "documentationItems") or []
        doc_item = DocumentationItem(
            chunk_id=chunk_id,
            source="upload",
            doc_id=doc_id,
            url=None,
            summary=None,
            scrape_job_ids=[],
            content=doc_text,
            metadata={"filename": documentation.filename or "unknown", "length": len(doc_text)},
        )
        doc_dict = doc_item.model_dump(by_alias=True, mode="json")
        existing_docs.append(doc_dict)
        await repo.update_session(session_id, {"documentationItems": existing_docs})
        await db.commit()

        return [doc_dict]

    doc_items = await repo.get_session_data(session_id, "documentationItems")
    if doc_items and len(doc_items) > 0:
        return doc_items

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
