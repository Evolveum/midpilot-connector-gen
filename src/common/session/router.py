# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
import uuid
from typing import Any, Dict
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Path, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...common.chunks import split_text_with_token_overlap
from ...common.database.config import get_db
from ...common.database.repositories.job_repository import JobRepository
from ...common.database.repositories.session_repository import SessionRepository
from ...common.enums import JobStage
from ...common.jobs import schedule_coroutine_job
from ...config import config
from .schema import SessionCreateResponse, SessionDataResponse, SessionUpdateRequest
from .session import process_documentation_worker

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "",
    response_model=SessionCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new session",
)
async def create_session(db: AsyncSession = Depends(get_db)) -> SessionCreateResponse:
    """
    Create a new session and return the session ID.
    """
    repo = SessionRepository(db)
    try:
        session_id = await repo.create_session()
    except Exception as e:
        logger.error(f"Failed to create session: {e}")
        raise HTTPException(status_code=500, detail="Unable to create session")
    return SessionCreateResponse(
        sessionId=session_id,
        message="Session created successfully. Use this session_id in subsequent requests.",
    )


@router.post(
    "/{session_id}",
    response_model=SessionCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new session with provided ID",
)
async def create_session_with_id(
    session_id: UUID = Path(..., description="Session ID"),
    db: AsyncSession = Depends(get_db),
) -> SessionCreateResponse:
    """
    Create a new session using the provided session ID.
    Returns 409 if the session already exists.
    """
    repo = SessionRepository(db)
    if await repo.session_exists(session_id):
        logger.error(f"Cannot create session - session already exists: {session_id}")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Session {session_id} already exists")

    try:
        created_id = await repo.create_session_with_id(session_id)
    except ValueError as e:
        logger.error(f"Failed to create session with ID {session_id}: {e}")
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create session with ID {session_id}: {e}")
        raise HTTPException(status_code=500, detail="Unable to create session")

    return SessionCreateResponse(
        sessionId=created_id,
        message="Session created successfully with provided ID.",
    )


@router.get(
    "/{session_id}",
    response_model=SessionDataResponse,
    summary="Get session data",
)
async def get_session(
    session_id: UUID = Path(..., description="Session ID"), db: AsyncSession = Depends(get_db)
) -> SessionDataResponse:
    """
    Retrieve session data by session ID.
    """
    repo = SessionRepository(db)
    session = await repo.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    return SessionDataResponse(
        sessionId=session["sessionId"],
        data=session["data"],
        createdAt=session["createdAt"],
        updatedAt=session["updatedAt"],
    )


@router.head("/{session_id}", summary="Check if session exists", status_code=204)
async def check_session_exists(
    session_id: UUID = Path(..., description="Session ID"), db: AsyncSession = Depends(get_db)
) -> None:
    """
    Check if a session exists by session ID.
    Returns 204 No Content if exists, 404 Not Found if not.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")


@router.patch(
    "/{session_id}",
    status_code=status.HTTP_200_OK,
    summary="Update session data",
)
async def update_session(
    request: SessionUpdateRequest,
    session_id: UUID = Path(..., description="Session ID"),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Update session data. Merges provided data with existing session data.
    """
    repo = SessionRepository(db)
    success = await repo.update_session(session_id, request.data)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found or update failed"
        )
    return {"message": "Session updated successfully", "sessionId": session_id}


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a session",
)
async def delete_session(
    session_id: UUID = Path(..., description="Session ID"), db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Delete a session and all associated data.
    """
    repo = SessionRepository(db)
    success = await repo.delete_session(session_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")
    return {"message": "Session deleted successfully", "sessionId": session_id}


@router.get(
    "/{session_id}/jobs",
    summary="List all jobs in session",
)
async def list_session_jobs(
    session_id: UUID = Path(..., description="Session ID"), db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    List all jobs associated with this session.
    Returns job IDs and their current status.
    """
    session_repo = SessionRepository(db)
    if not await session_repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    job_repo = JobRepository(db)
    jobs = await job_repo.get_jobs_by_session(session_id)

    return {"sessionId": session_id, "jobs": jobs}


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
    session_repo = SessionRepository(db)
    if not await session_repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    job_repo = JobRepository(db)
    jobs = await job_repo.get_jobs_by_session(session_id)

    # Filter for documentation upload jobs
    upload_jobs = [job for job in jobs if job.get("type", "").startswith("documentation.processUpload")]

    return {
        "sessionId": session_id,
        "uploadJobs": upload_jobs,
        "totalUploads": len(upload_jobs),
    }


# Documentation Management
@router.post("/{session_id}/documentation", summary="Upload documentation to session")
async def upload_documentation(
    session_id: UUID = Path(..., description="Session ID"),
    documentation: UploadFile = File(..., description="OpenAPI/Swagger YAML or JSON file"),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Upload, chunk, and process documentation in the session.
    Creates a job and queues it for processing - returns immediately with job_id.
    Each chunk becomes a separate DocumentationItem with source='upload'.
    Application name and version are loaded from session's discoveryInput or scrapeInput.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    # Load app and app_version from session
    session_data = await repo.get_session_data(session_id) or {}
    discovery_input = session_data.get("discoveryInput", {})
    scrape_input = session_data.get("scrapeInput", {})

    # Try discoveryInput first, fallback to scrapeInput, then to "unknown"
    app = discovery_input.get("applicationName") or scrape_input.get("applicationName") or "unknown"
    app_version = discovery_input.get("applicationVersion") or scrape_input.get("applicationVersion") or "unknown"

    doc_text = (await documentation.read()).decode("utf-8", errors="ignore")
    filename = documentation.filename or "unknown"

    # Chunk the content
    logger.info("[Upload] Chunking documentation for session %s", session_id)
    chunks = split_text_with_token_overlap(
        doc_text, max_tokens=config.scrape_and_process.chunk_length, overlap_ratio=0.05
    )
    logger.info("[Upload] Generated %s chunks for uploaded document", len(chunks))

    page_id = uuid.uuid4()  # Single page_id for the entire uploaded file

    # Create job with the processing logic
    job_id = await schedule_coroutine_job(
        job_type="documentation.processUpload",
        input_payload={
            "session_id": str(session_id),
            "filename": filename,
            "page_id": str(page_id),
            "chunks_count": len(chunks),
        },
        worker=process_documentation_worker,
        worker_kwargs={
            "session_id": session_id,
            "chunks": chunks,
            "filename": filename,
            "page_id": page_id,
            "app": app,
            "app_version": app_version,
        },
        initial_stage=JobStage.processing,
        initial_message=f"Processing {len(chunks)} chunks",
        session_id=session_id,
    )

    # Store job_id in session
    job_key = f"documentation.processUpload_{page_id}_job_id"
    await repo.update_session(session_id, {job_key: str(job_id)})

    # Return immediately with job info
    return {
        "message": "Documentation upload queued for processing",
        "sessionId": session_id,
        "jobId": job_id,
        "pageId": str(page_id),
        "chunksToProcess": len(chunks),
        "status": "queued",
    }


@router.get("/{session_id}/documentation", summary="Get documentation from session")
async def get_documentation(
    session_id: UUID = Path(..., description="Session ID"), db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Retrieve all documentation items stored in the session.
    Returns both scraped and uploaded documentation.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    doc_items = await repo.get_session_data(session_id, "documentationItems")
    if not doc_items:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No documentation found in session {session_id}"
        )

    return {"sessionId": session_id, "documentationItems": doc_items, "count": len(doc_items)}


@router.put("/{session_id}/documentation", summary="Replace all documentation in session")
async def replace_documentation(
    session_id: UUID = Path(..., description="Session ID"),
    documentation: UploadFile = File(..., description="OpenAPI/Swagger YAML or JSON file"),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Replace all existing documentation in the session with a single uploaded file.
    This clears all previously scraped and uploaded documentation.
    Chunks and processes the documentation with LLM - returns immediately with job_id.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    # Load app and app_version from session
    session_data = await repo.get_session_data(session_id) or {}
    discovery_input = session_data.get("discoveryInput", {})
    scrape_input = session_data.get("scrapeInput", {})

    # Try discoveryInput first, fallback to scrapeInput, then to "unknown"
    app = discovery_input.get("applicationName") or scrape_input.get("applicationName") or "unknown"
    app_version = discovery_input.get("applicationVersion") or scrape_input.get("applicationVersion") or "unknown"

    doc_text = (await documentation.read()).decode("utf-8", errors="ignore")
    filename = documentation.filename or "unknown"

    # Clear existing documentation first
    await repo.update_session(session_id, {"documentationItems": []})

    # Chunk the content
    logger.info("[Upload] Chunking documentation for session %s", session_id)
    chunks = split_text_with_token_overlap(
        doc_text, max_tokens=config.scrape_and_process.chunk_length, overlap_ratio=0.05
    )
    logger.info("[Upload] Generated %s chunks for uploaded document", len(chunks))

    page_id = uuid.uuid4()  # Single page_id for the entire uploaded file

    # Create job with the processing logic
    job_id = await schedule_coroutine_job(
        job_type="documentation.processUpload",
        input_payload={
            "session_id": str(session_id),
            "filename": filename,
            "page_id": str(page_id),
            "chunks_count": len(chunks),
        },
        worker=process_documentation_worker,
        worker_kwargs={
            "session_id": session_id,
            "chunks": chunks,
            "filename": filename,
            "page_id": page_id,
            "app": app,
            "app_version": app_version,
        },
        initial_stage=JobStage.processing,
        initial_message=f"Processing {len(chunks)} chunks",
        session_id=session_id,
    )

    # Store job_id in session
    job_key = f"documentation.processUpload_{page_id}_job_id"
    await repo.update_session(session_id, {job_key: str(job_id)})

    # Return immediately with job info
    return {
        "message": "Documentation replacement queued for processing",
        "sessionId": session_id,
        "jobId": job_id,
        "pageId": str(page_id),
        "chunksToProcess": len(chunks),
        "status": "queued",
    }


@router.put("/{session_id}/documentation/{documentation_id}", summary="Upload documentation to session")
async def upload_documentation_by_id(
    session_id: UUID = Path(..., description="Session ID"),
    documentation_id: UUID = Path(..., description="Documentation UUID (used as page_id)"),
    documentation: UploadFile = File(..., description="OpenAPI/Swagger YAML or JSON file"),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Upload, chunk, and process documentation in the session with a specific page_id.
    Creates a job and queues it for processing - returns immediately with job_id.
    Each chunk becomes a separate DocumentationItem with source='upload' and the provided documentation_id as page_id.
    Application name and version are loaded from session's discoveryInput or scrapeInput.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    # Load app and app_version from session
    session_data = await repo.get_session_data(session_id) or {}
    discovery_input = session_data.get("discoveryInput", {})
    scrape_input = session_data.get("scrapeInput", {})

    # Try discoveryInput first, fallback to scrapeInput, then to "unknown"
    app = discovery_input.get("applicationName") or scrape_input.get("applicationName") or "unknown"
    app_version = discovery_input.get("applicationVersion") or scrape_input.get("applicationVersion") or "unknown"

    doc_text = (await documentation.read()).decode("utf-8", errors="ignore")
    filename = documentation.filename or "unknown"

    # Chunk the content
    logger.info("[Upload] Chunking documentation for session %s with page_id %s", session_id, documentation_id)
    chunks = split_text_with_token_overlap(
        doc_text, max_tokens=config.scrape_and_process.chunk_length, overlap_ratio=0.05
    )
    logger.info("[Upload] Generated %s chunks for uploaded document", len(chunks))

    page_id = documentation_id  # Use the provided documentation_id as page_id

    # Create job with the processing logic
    job_id = await schedule_coroutine_job(
        job_type="documentation.processUpload",
        input_payload={
            "session_id": str(session_id),
            "filename": filename,
            "page_id": str(page_id),
            "chunks_count": len(chunks),
        },
        worker=process_documentation_worker,
        worker_kwargs={
            "session_id": session_id,
            "chunks": chunks,
            "filename": filename,
            "page_id": page_id,
            "app": app,
            "app_version": app_version,
        },
        initial_stage=JobStage.processing,
        initial_message=f"Processing {len(chunks)} chunks",
        session_id=session_id,
    )

    # Store job_id in session
    job_key = f"documentation.processUpload_{page_id}_job_id"
    await repo.update_session(session_id, {job_key: str(job_id)})

    # Return immediately with job info
    return {
        "message": "Documentation upload queued for processing",
        "sessionId": session_id,
        "jobId": job_id,
        "pageId": str(page_id),
        "chunksToProcess": len(chunks),
        "status": "queued",
    }


@router.delete("/{session_id}/documentation", summary="Delete all documentation from session")
async def delete_documentation(
    session_id: UUID = Path(..., description="Session ID"), db: AsyncSession = Depends(get_db)
) -> dict:
    """
    Remove all documentation (both scraped and uploaded) from the session.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    await repo.update_session(session_id, {"documentationItems": []})
    return {"message": "All documentation deleted successfully", "sessionId": session_id}


@router.head(
    "/{session_id}/documentation/{documentation_id}",
    summary="Checks if documentation item exists by UUID",
    status_code=204,
)
async def check_documentation_item(
    session_id: UUID = Path(..., description="Session ID"),
    documentation_id: UUID = Path(..., description="Documentation UUID"),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Checks a single documentation item from the session by its UUID.
    Returns 404 if the session or the documentation item is not found.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    doc_items: list[dict] = await repo.get_session_data(session_id, "documentationItems") or []
    if not doc_items:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No documentation found in session {session_id}"
        )

    # Find item by UUID
    index_to_check = next((i for i, d in enumerate(doc_items) if str(d.get("pageId")) == str(documentation_id)), None)
    if index_to_check is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Documentation {documentation_id} not found in session {session_id}",
        )

    return


@router.delete("/{session_id}/documentation/{documentation_id}", summary="Delete all documentation chunks by UUID")
async def delete_documentation_item(
    session_id: UUID = Path(..., description="Session ID"),
    documentation_id: UUID = Path(..., description="Documentation page_id (deletes all chunks with this page_id)"),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Delete all documentation chunks with the specified page_id from the session.
    Since uploaded documentation is chunked, this removes all chunks belonging to the same document.
    Returns 404 if the session or any documentation with that page_id is not found.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    doc_items: list[dict] = await repo.get_session_data(session_id, "documentationItems") or []
    if not doc_items:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No documentation found in session {session_id}"
        )

    # Filter out all items with the specified page_id
    initial_count = len(doc_items)
    filtered_items = [item for item in doc_items if item.get("pageId") != str(documentation_id)]
    deleted_count = initial_count - len(filtered_items)

    if deleted_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Documentation with documentation_id {documentation_id} not found in session {session_id}",
        )

    # Update session with filtered items
    await repo.update_session(session_id, {"documentationItems": filtered_items})

    return {
        "message": f"Documentation deleted successfully ({deleted_count} chunk(s) removed)",
        "sessionId": session_id,
        "deletedPageId": str(documentation_id),
        "deletedChunks": deleted_count,
    }
