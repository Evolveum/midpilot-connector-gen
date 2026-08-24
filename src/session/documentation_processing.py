# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Background worker that parses, chunks and LLM-processes uploaded documentation."""

import asyncio
import json
import logging
from typing import Any, Dict
from uuid import UUID

from src.config import config
from src.core.concurrency import TaskScope
from src.core.db import async_session_maker
from src.database.repositories.documentation_repository import DocumentationRepository, DocumentationWriteBatch
from src.documents.processing.llms import get_llm_processed_chunk
from src.documents.processing.processor import build_chunk_metadata
from src.documents.processing.prompts import get_llm_chunk_process_prompt
from src.documents.processing.schema import LlmChunkOutput
from src.jobs import increment_processed_documents, update_job_progress
from src.jobs.errors import JobClaimLostError
from src.session.documentation_upload import (
    chunk_uploaded_documentation,
    parse_uploaded_documentation,
)
from src.session.info_metadata import get_session_api_types, resolve_session_api_type
from src.session.schema import ProcessedDocumentationChunk, RawUploadedDocumentation
from src.shared.content_types import (
    detect_conndev_api_type,
    is_conndev_content_type,
    is_conndev_object_class_document,
)
from src.shared.enums import ApiType, JobStage

logger = logging.getLogger(__name__)

_UPLOAD_WORKER_LIMIT = max(1, min(config.database.pool_size, 8))
_UPLOAD_WORKER_SEMAPHORE = asyncio.Semaphore(_UPLOAD_WORKER_LIMIT)


async def _resolve_conndev_api_type(document: Any, filename: str, session_id: UUID) -> ApiType | None:
    """
    Resolve the protocol of one Conndev document, preferring its own binding.

    An embedded sub-class export with no attributes (``Entitlement__typeInfo``) declares no
    binding anywhere, so there is nothing in the document to contradict: it falls back to the
    protocol the session already detected. Any other unbound document stays unclassified.
    """
    api_type = detect_conndev_api_type(document)
    if api_type is not None:
        return api_type

    if not is_conndev_object_class_document(document):
        return None

    api_type = resolve_session_api_type(await get_session_api_types(session_id))
    logger.info(
        "[Session:Upload] Conndev export %s declares no protocol binding; using the session protocol %s",
        filename,
        api_type.value,
    )
    return api_type


async def _build_conndev_chunk_output(chunk_text: str, filename: str, session_id: UUID) -> LlmChunkOutput:
    """Build deterministic processing metadata from a Conndev document's protocol binding."""
    try:
        document = json.loads(chunk_text)
    except json.JSONDecodeError:
        document = None

    api_type = await _resolve_conndev_api_type(document, filename, session_id)
    if api_type is None:
        logger.warning("[Session:Upload] Could not determine protocol for Conndev export %s", filename)
        summary = f"midPoint connector-development export: {filename}"
        tags = ["schema", "conndev"]
    else:
        summary = f"midPoint connector-development {api_type.name} export: {filename}"
        tags = [api_type.value, "schema", "conndev"]

    return LlmChunkOutput(
        summary=summary,
        num_endpoints=0,
        tags=tags,
        category="spec_json",
        different_app_name=False,
        num_defined_object_classes=1,
    )


async def _persist_processed_documentation_chunk(
    *,
    repository: DocumentationRepository,
    session_id: UUID,
    doc_id: UUID,
    job_id: UUID,
    filename: str,
    chunk: ProcessedDocumentationChunk,
) -> None:
    await repository.create_documentation_item(
        session_id=session_id,
        source="upload",
        content=chunk.text,
        doc_id=doc_id,
        original_job_id=job_id,
        url=f"upload://{filename}",
        summary=chunk.summary,
        metadata=chunk.metadata,
    )


async def _persist_processed_documentation_batch(
    *,
    session_id: UUID,
    doc_id: UUID,
    job_id: UUID,
    filename: str,
    chunks: list[ProcessedDocumentationChunk],
) -> None:
    async with async_session_maker() as db:
        repository = DocumentationRepository(db)
        write_batch = DocumentationWriteBatch(db, config.jobs.documentation_write_batch_size)
        for chunk in chunks:
            await _persist_processed_documentation_chunk(
                repository=repository,
                session_id=session_id,
                doc_id=doc_id,
                job_id=job_id,
                filename=filename,
                chunk=chunk,
            )
            await write_batch.record_write()
        await write_batch.commit_pending()


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
            "[Session:Upload:Job] Processing %s chunks for session %s (job %s) [worker_limit=%s]",
            len(chunks),
            session_id,
            job_id,
            _UPLOAD_WORKER_LIMIT,
        )

        async def process_chunk(idx: int, chunk_data: tuple[str, int]) -> ProcessedDocumentationChunk:
            chunk_text, chunk_length = chunk_data

            if is_conndev_content_type(uploaded.content_type):
                data = await _build_conndev_chunk_output(chunk_text, uploaded.filename, session_id)
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
        persist_errors: list[tuple[int, Exception]] = []
        persistence_buffer: list[ProcessedDocumentationChunk] = []

        async def _flush_persistence_buffer() -> None:
            if not persistence_buffer:
                return
            chunks_to_persist = list(persistence_buffer)
            persistence_buffer.clear()
            try:
                await _persist_processed_documentation_batch(
                    session_id=session_id,
                    doc_id=doc_id,
                    job_id=job_id,
                    filename=uploaded.filename,
                    chunks=chunks_to_persist,
                )
            except JobClaimLostError:
                raise
            except Exception as e:
                logger.error(
                    "[Session:Upload:Job] Failed to persist chunk batch %s for session %s (job %s): %s",
                    [chunk.index for chunk in chunks_to_persist],
                    session_id,
                    job_id,
                    e,
                )
                persist_errors.extend((chunk.index, e) for chunk in chunks_to_persist)

        async with TaskScope(f"upload-chunks:{job_id}") as chunk_scope:
            tasks = [chunk_scope.start(process_chunk(i, ch)) for i, ch in enumerate(chunks)]
            try:
                for completed_task in asyncio.as_completed(tasks):
                    processed_chunk = await completed_task
                    completed_chunks[processed_chunk.index] = processed_chunk
                    await increment_processed_documents(job_id, delta=1)

                    while next_chunk_to_persist in completed_chunks:
                        chunk_to_persist = completed_chunks.pop(next_chunk_to_persist)
                        persistence_buffer.append(chunk_to_persist)
                        next_chunk_to_persist += 1
                        if len(persistence_buffer) >= config.jobs.documentation_write_batch_size:
                            await _flush_persistence_buffer()

                await _flush_persistence_buffer()

            except JobClaimLostError:
                await chunk_scope.aclose()
                raise
            except Exception:
                await chunk_scope.aclose()
                persistence_buffer.extend(sorted(completed_chunks.values(), key=lambda c: c.index))
                await _flush_persistence_buffer()
                raise

        if persist_errors:
            failed_indices = sorted(idx for idx, _ in persist_errors)
            raise RuntimeError(
                f"Documentation upload partially failed: {len(persist_errors)} chunk(s) could not be persisted "
                f"(indices: {failed_indices})"
            )

        logger.info(
            "[Session:Upload:Job] Completed processing for session %s (job %s): generated %s chunks",
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
