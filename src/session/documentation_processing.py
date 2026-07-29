# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Background worker that parses, chunks and LLM-processes uploaded documentation."""

import asyncio
import logging
from typing import Any, Dict
from uuid import UUID

from src.config import config
from src.core.db import async_session_maker
from src.core.errors import JobClaimLostError
from src.database.repositories.documentation_repository import DocumentationRepository
from src.documents.processing.llms import get_llm_processed_chunk
from src.documents.processing.processor import build_chunk_metadata
from src.documents.processing.prompts import get_llm_chunk_process_prompt
from src.documents.processing.schema import LlmChunkOutput
from src.jobs import increment_processed_documents, update_job_progress
from src.session.documentation_upload import (
    chunk_uploaded_documentation,
    parse_uploaded_documentation,
)
from src.session.schema import ProcessedDocumentationChunk, RawUploadedDocumentation
from src.shared.content_types import is_conndev_content_type
from src.shared.enums import JobStage

logger = logging.getLogger(__name__)

_UPLOAD_WORKER_LIMIT = max(1, min(config.database.pool_size, 8))
_UPLOAD_WORKER_SEMAPHORE = asyncio.Semaphore(_UPLOAD_WORKER_LIMIT)


async def _persist_processed_documentation_chunk(
    *,
    session_id: UUID,
    doc_id: UUID,
    job_id: UUID,
    filename: str,
    chunk: ProcessedDocumentationChunk,
) -> None:
    async with async_session_maker() as db:
        doc_repo = DocumentationRepository(db)
        await doc_repo.create_documentation_item(
            session_id=session_id,
            source="upload",
            content=chunk.text,
            doc_id=doc_id,
            original_job_id=job_id,
            url=f"upload://{filename}",
            summary=chunk.summary,
            metadata=chunk.metadata,
        )
        await db.commit()


async def process_documentation_worker(
    session_id: UUID,
    raw_upload: RawUploadedDocumentation,
    doc_id: UUID,
    app: str,
    app_version: str,
    job_id: UUID,
) -> Dict[str, Any]:
    async with _UPLOAD_WORKER_SEMAPHORE:
        await update_job_progress(
            job_id,
            stage=JobStage.processing,
            message=f"Parsing uploaded documentation {raw_upload.filename}",
            processing_completed=0,
        )
        uploaded = await parse_uploaded_documentation(raw_upload)
        chunks = chunk_uploaded_documentation(session_id, uploaded)
        semaphore = asyncio.Semaphore(config.scrape_and_process.max_concurrent)

        await update_job_progress(
            job_id,
            stage=JobStage.processing_chunks,
            message=f"Processing {len(chunks)} chunks",
            total_processing=len(chunks),
            processing_completed=0,
        )

        logger.info(
            "[Upload:Job] Processing %s chunks for session %s (job %s) [worker_limit=%s]",
            len(chunks),
            session_id,
            job_id,
            _UPLOAD_WORKER_LIMIT,
        )

        async def process_chunk(idx: int, chunk_data: tuple[str, int]) -> ProcessedDocumentationChunk:
            chunk_text, chunk_length = chunk_data

            if is_conndev_content_type(uploaded.content_type):
                data = LlmChunkOutput(
                    summary=f"midPoint connector-development SCIM export: {uploaded.filename}",
                    num_endpoints=0,
                    tags=["scim", "schema", "conndev"],
                    category="spec_json",
                    different_app_name=False,
                    num_defined_object_classes=1,
                )
            else:
                async with semaphore:
                    prompts = get_llm_chunk_process_prompt(chunk_text, uploaded.filename, app, app_version)
                    data = await get_llm_processed_chunk(prompts)

            metadata = build_chunk_metadata(
                chunk_number=idx,
                token_count=chunk_length,
                character_count=len(chunk_text),
                data=data,
                content_type=uploaded.content_type,
                filename=uploaded.filename,
                extra=uploaded.metadata,
            )

            return ProcessedDocumentationChunk(index=idx, text=chunk_text, summary=data.summary, metadata=metadata)

        completed_chunks: dict[int, ProcessedDocumentationChunk] = {}
        next_chunk_to_persist = 0
        tasks = [asyncio.create_task(process_chunk(i, ch)) for i, ch in enumerate(chunks)]
        persist_errors: list[tuple[int, Exception]] = []

        async def _try_persist(chunk: ProcessedDocumentationChunk) -> None:
            try:
                await _persist_processed_documentation_chunk(
                    session_id=session_id,
                    doc_id=doc_id,
                    job_id=job_id,
                    filename=uploaded.filename,
                    chunk=chunk,
                )
            except JobClaimLostError:
                raise
            except Exception as e:
                logger.error(
                    "[Upload:Job] Failed to persist chunk %s for session %s (job %s): %s",
                    chunk.index,
                    session_id,
                    job_id,
                    e,
                )
                persist_errors.append((chunk.index, e))

        try:
            for completed_task in asyncio.as_completed(tasks):
                processed_chunk = await completed_task
                completed_chunks[processed_chunk.index] = processed_chunk
                await increment_processed_documents(job_id, delta=1)

                while next_chunk_to_persist in completed_chunks:
                    chunk_to_persist = completed_chunks.pop(next_chunk_to_persist)
                    await _try_persist(chunk_to_persist)
                    next_chunk_to_persist += 1

        except JobClaimLostError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        except Exception:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for buffered_chunk in sorted(completed_chunks.values(), key=lambda c: c.index):
                await _try_persist(buffered_chunk)
            raise

        if persist_errors:
            failed_indices = sorted(idx for idx, _ in persist_errors)
            raise RuntimeError(
                f"Documentation upload partially failed: {len(persist_errors)} chunk(s) could not be persisted "
                f"(indices: {failed_indices})"
            )

        logger.info(
            "[Upload:Job] Completed processing for session %s (job %s): generated %s chunks",
            session_id,
            job_id,
            len(chunks),
        )

        return {
            "chunks_processed": len(chunks),
            "doc_id": doc_id,
            "filename": uploaded.filename,
            "content_type": uploaded.content_type,
        }
