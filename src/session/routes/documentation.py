# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Session-scoped documentation endpoints (upload, export, import, delete).

Documentation is a session-owned resource, so these routes live in the session
context and are mounted under the same /session prefix as the session routes -
the URL surface is unchanged by the router split.
"""

import asyncio
import logging
import uuid
from typing import Any, Dict
from uuid import UUID

from fastapi import APIRouter, Depends, File, Path, Query, Response, UploadFile, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import config
from src.core.db import get_db
from src.database.repositories.documentation_repository import DocumentationRepository
from src.database.repositories.job_repository import JobRepository
from src.database.repositories.session_repository import SessionRepository
from src.session import service
from src.session.access import ensure_session_exists
from src.session.documentation_upload import prepare_documentation_upload, queue_documentation_upload_job
from src.session.errors import (
    DocumentationImportConflictError,
    DocumentationItemNotFoundError,
    DocumentationNotFoundError,
)
from src.session.schema import Documentation
from src.session.service import build_group_documentation_response
from src.shared.enums import JobStatus

logger = logging.getLogger(__name__)

router = APIRouter()

# Guard high-burst documentation HEAD/PUT traffic against DB pool exhaustion.
_DOC_UPLOAD_API_LIMIT = max(1, min(config.database.pool_size, 8))
_DOC_UPLOAD_API_SEMAPHORE = asyncio.Semaphore(_DOC_UPLOAD_API_LIMIT)


# GET Endpoints
@router.get(
    "/{session_id}/documentation/status",
    summary="Get documentation upload processing status",
)
async def get_documentation_upload_status(
    session_id: UUID = Path(..., description="Session ID"), db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get the status of all documentation upload jobs for this session.
    Returns information about queued, running, completed, and failed uploads.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    job_repo = JobRepository(db)
    upload_jobs = await service.list_documentation_upload_jobs(job_repo, session_id)

    return {
        "sessionId": session_id,
        "uploadJobs": upload_jobs,
        "totalUploads": len(upload_jobs),
    }


@router.get("/{session_id}/documentation", summary="Get documentation from session")
async def get_documentation(
    session_id: UUID = Path(..., description="Session ID"), db: AsyncSession = Depends(get_db)
) -> list[Documentation]:
    """
    Retrieve all documentation items stored in the session grouped by document.
    Returns list of document bundles with docId and all its chunks.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    doc_repo = DocumentationRepository(db)
    doc_rows = await doc_repo.get_documentation_items_for_export(session_id)
    if not doc_rows:
        raise DocumentationNotFoundError(session_id)

    return build_group_documentation_response(doc_rows)


@router.get(
    "/{session_id}/documentation/{documentation_id}",
    summary="Get documentation by document ID",
)
async def get_documentation_by_id(
    session_id: UUID = Path(..., description="Session ID"),
    documentation_id: UUID = Path(..., description="Documentation UUID (doc_id)"),
    db: AsyncSession = Depends(get_db),
) -> Documentation:
    """
    Retrieve all chunks for a single documentation document (doc_id).
    Returns one document bundle in the same shape as export, scoped to one doc_id.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    doc_repo = DocumentationRepository(db)
    return await service.get_documentation_document(doc_repo, session_id, documentation_id)


# HEAD Endpoints
@router.head(
    "/{session_id}/documentation/{documentation_id}",
    summary="Checks if documentation item exists by documentation_id",
    status_code=204,
    responses={
        202: {
            "description": "Documentation upload is still being processed",
        }
    },
)
async def check_documentation_item(
    session_id: UUID = Path(..., description="Session ID"),
    documentation_id: UUID = Path(..., description="Documentation ID"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """
    Checks a single documentation item from the session by its UUID.
    Returns 404 if the session or the documentation item is not found.
    """
    async with _DOC_UPLOAD_API_SEMAPHORE:
        repo = SessionRepository(db)
        await ensure_session_exists(repo, session_id)

        doc_repo = DocumentationRepository(db)
        if await doc_repo.get_documentation_items_by_doc_id(session_id, documentation_id):
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        # If the item is not yet persisted, check if an upload job for this doc is queued/running.
        job_key = f"documentation.processUpload_{documentation_id}_job_id"
        pending_job_id = await repo.get_session_data(session_id, job_key)
        if pending_job_id:
            job_repo = JobRepository(db)
            job_status = await job_repo.get_job_status(UUID(str(pending_job_id)))
            if job_status.get("status") in {JobStatus.queued.value, JobStatus.running.value}:
                return Response(
                    status_code=status.HTTP_202_ACCEPTED,
                    headers={
                        "X-Documentation-Status": "processing",
                        "X-Job-Id": str(pending_job_id),
                    },
                )

    raise DocumentationItemNotFoundError(documentation_id, session_id)


# Documentation Management
async def _queue_documentation_upload(
    db: AsyncSession,
    session_id: UUID,
    documentation: UploadFile,
    *,
    doc_id: UUID,
    message: str,
    skip_cache: bool = False,
    clear_existing: bool = False,
) -> Dict[str, Any]:
    """
    Shared flow for the documentation upload endpoints: validate the session,
    prepare the upload (optionally clearing existing documentation first), and
    queue the processing job. Returns immediately with the job/doc identifiers.

    The semaphore guards high-burst upload traffic against DB pool exhaustion.
    """
    async with _DOC_UPLOAD_API_SEMAPHORE:
        repo = SessionRepository(db)
        await ensure_session_exists(repo, session_id)

        prepared = await prepare_documentation_upload(repo, session_id, documentation)

        if clear_existing:
            await DocumentationRepository(db).delete_documentation_items_by_session(session_id)

    job_id = await queue_documentation_upload_job(
        repo=repo,
        session_id=session_id,
        doc_id=doc_id,
        prepared=prepared,
        skip_cache=skip_cache,
    )

    return {
        "message": message,
        "sessionId": session_id,
        "jobId": job_id,
        "docId": str(doc_id),
        "status": "queued",
    }


@router.post("/{session_id}/documentation", summary="Upload documentation to session")
async def upload_documentation(
    session_id: UUID = Path(..., description="Session ID"),
    documentation: UploadFile = File(..., description="Documentation file"),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Upload, chunk, and process documentation in the session.
    Creates a job and queues it for processing - returns immediately with job_id.
    Each chunk becomes a separate DocumentationItem with source='upload'.
    Application name and version are loaded from session's discoveryInput or scrapeInput.
    """
    return await _queue_documentation_upload(
        db,
        session_id,
        documentation,
        doc_id=uuid.uuid4(),  # Single doc_id for the entire uploaded file
        message="Documentation upload queued for processing",
    )


@router.post("/{session_id}/documentation/{documentation_id}", summary="Upload documentation to session by doc_id")
async def upload_documentation_by_id(
    session_id: UUID = Path(..., description="Session ID"),
    documentation_id: UUID = Path(..., description="Documentation ID"),
    documentation: UploadFile = File(..., description="Documentation file"),
    skip_cache: bool = Query(False, alias="skipCache", description="Whether to skip cached data"),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Process uploaded documentation file using LLM.
    Creates a job and queues it for processing - returns immediately with job_id.
    Each chunk becomes a separate DocumentationItem with source='upload' and the provided documentation_id as doc_id.
    Application name and version are loaded from session's discoveryInput or scrapeInput.
    """
    return await _queue_documentation_upload(
        db,
        session_id,
        documentation,
        doc_id=documentation_id,  # Use the provided documentation_id as doc_id
        message="Documentation upload queued for processing",
        skip_cache=skip_cache,
    )


# PUT Endpoints
@router.put("/{session_id}/documentation", summary="Replace all documentation in session")
async def replace_documentation(
    session_id: UUID = Path(..., description="Session ID"),
    documentation: UploadFile = File(..., description="Documentation file"),
    skip_cache: bool = Query(False, alias="skipCache", description="Whether to skip cached data"),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Replace all existing documentation in the session with a single uploaded file.
    This clears all previously scraped and uploaded documentation.
    Chunks and processes the documentation with LLM - returns immediately with job_id.
    """
    return await _queue_documentation_upload(
        db,
        session_id,
        documentation,
        doc_id=uuid.uuid4(),  # Single doc_id for the entire uploaded file
        message="Documentation replacement queued for processing",
        skip_cache=skip_cache,
        clear_existing=True,
    )


@router.put(
    "/{session_id}/documentation/{documentation_id}",
    summary="Import documentation preprocessed by LLM",
)
async def import_documentation_by_id(
    document: Documentation,
    session_id: UUID = Path(..., description="Session ID"),
    documentation_id: UUID = Path(..., description="Documentation UUID (doc_id)"),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Replace one documentation document (doc_id) with provided chunks.
    Other documents in the session remain unchanged.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    doc_repo = DocumentationRepository(db)
    try:
        imported_chunks = await service.import_documentation_document(doc_repo, session_id, documentation_id, document)
    except IntegrityError as exc:
        # The driver message names constraints, columns and sometimes row values;
        # it belongs in the log, not in the response.
        logger.exception("[Session:Documentation] Documentation import conflicted with existing data")
        raise DocumentationImportConflictError(documentation_id) from exc

    return {
        "message": "Documentation imported successfully",
        "sessionId": str(session_id),
        "docId": str(documentation_id),
        "importedChunks": imported_chunks,
    }


# DELETE Endpoints
@router.delete("/{session_id}/documentation", summary="Delete all documentation from session")
async def delete_documentation(
    session_id: UUID = Path(..., description="Session ID"), db: AsyncSession = Depends(get_db)
) -> dict:
    """
    Remove all documentation (both scraped and uploaded) from the session.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    doc_repo = DocumentationRepository(db)
    await doc_repo.delete_documentation_items_by_session(session_id)
    return {"message": "All documentation deleted successfully", "sessionId": session_id}


@router.delete("/{session_id}/documentation/{documentation_id}", summary="Delete all documentation chunks by UUID")
async def delete_documentation_item(
    session_id: UUID = Path(..., description="Session ID"),
    documentation_id: UUID = Path(..., description="Documentation doc_id (deletes all chunks with this doc_id)"),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Delete all documentation chunks with the specified doc_id from the session.
    Since uploaded documentation is chunked, this removes all chunks belonging to the same document.
    Returns 404 if the session or any documentation with that doc_id is not found.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    doc_repo = DocumentationRepository(db)
    deleted_count = await service.delete_documentation_document(doc_repo, session_id, documentation_id)

    return {
        "message": f"Documentation deleted successfully ({deleted_count} chunk(s) removed)",
        "sessionId": session_id,
        "deletedDocId": str(documentation_id),
        "deletedChunks": deleted_count,
    }
