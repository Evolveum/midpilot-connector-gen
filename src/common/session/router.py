# Copyright (c) 2025 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Core session management endpoints for V2 API.
Handles session CRUD and documentation management only.
"""

import asyncio
import logging
import uuid
from typing import Any, Dict, List
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Path, UploadFile, status

from ...common.chunk_processor.llms import get_llm_processed_chunk
from ...common.chunk_processor.prompts import get_llm_chunk_process_prompt
from ...common.chunks import split_text_with_token_overlap
from ...common.enums import JobStage
from ...common.jobs import (
    get_job_status,
    increment_processed_documents,
    schedule_coroutine_job,
    update_job_progress,
)
from ...config import config
from .schema import DocumentationItem, SessionCreateResponse, SessionDataResponse, SessionUpdateRequest
from .session import SessionManager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "",
    response_model=SessionCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new session",
)
async def create_session() -> SessionCreateResponse:
    """
    Create a new session and return the session ID.
    """
    try:
        session_id = SessionManager.create_session()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
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
) -> SessionCreateResponse:
    """
    Create a new session using the provided session ID.
    Returns 409 if the session already exists.
    """
    if SessionManager.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Session {session_id} already exists")

    try:
        created_id = SessionManager.create_session_with_id(session_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return SessionCreateResponse(
        sessionId=created_id,
        message="Session created successfully with provided ID.",
    )


@router.get(
    "/{session_id}",
    response_model=SessionDataResponse,
    summary="Get session data",
)
async def get_session(session_id: UUID = Path(..., description="Session ID")) -> SessionDataResponse:
    """
    Retrieve session data by session ID.
    """
    session = SessionManager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    return SessionDataResponse(
        sessionId=session["sessionId"],
        data=session["data"],
        createdAt=session["createdAt"],
        updatedAt=session["updatedAt"],
    )


@router.head("/{session_id}", summary="Check if session exists", status_code=204)
async def check_session_exists(session_id: UUID = Path(..., description="Session ID")) -> None:
    """
    Check if a session exists by session ID.
    Returns 204 No Content if exists, 404 Not Found if not.
    """
    session = SessionManager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")


@router.patch(
    "/{session_id}",
    status_code=status.HTTP_200_OK,
    summary="Update session data",
)
async def update_session(
    request: SessionUpdateRequest,
    session_id: UUID = Path(..., description="Session ID"),
) -> Dict[str, Any]:
    """
    Update session data. Merges provided data with existing session data.
    """
    success = SessionManager.update_session(session_id, request.data)
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
async def delete_session(session_id: UUID = Path(..., description="Session ID")) -> Dict[str, Any]:
    """
    Delete a session and all associated data.
    """
    success = SessionManager.delete_session(session_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")
    return {"message": "Session deleted successfully", "sessionId": session_id}


@router.get(
    "/{session_id}/jobs",
    summary="List all jobs in session",
)
async def list_session_jobs(session_id: UUID = Path(..., description="Session ID")) -> Dict[str, Any]:
    """
    List all jobs associated with this session.
    Returns job IDs and their current status.
    """
    if not SessionManager.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    session_data = SessionManager.get_session_data(session_id) or {}

    jobs: List[Dict[str, Any]] = []
    for key, value in session_data.items():
        if key.endswith("_job_id"):
            job_id = value
            job_status = get_job_status(job_id)
            jobs.append(
                {
                    "jobId": job_id,
                    "type": key.removesuffix("_job_id"),
                    "status": job_status.get("status", "unknown"),
                    "createdAt": job_status.get("createdAt"),
                }
            )

    return {"sessionId": session_id, "jobs": jobs}


# Documentation Management
@router.post("/{session_id}/documentation", summary="Upload documentation to session")
async def upload_documentation(
    session_id: UUID = Path(..., description="Session ID"),
    documentation: UploadFile = File(..., description="OpenAPI/Swagger YAML or JSON file"),
) -> Dict[str, Any]:
    """
    Upload, chunk, and process documentation in the session.
    Creates a job and queues it for processing - returns immediately with job_id.
    Each chunk becomes a separate DocumentationItem with source='upload'.
    Application name and version are loaded from session's discoveryInput or scrapeInput.
    """
    if not SessionManager.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    # Load app and app_version from session
    session_data = SessionManager.get_session_data(session_id) or {}
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
    job_id = schedule_coroutine_job(
        job_type="documentation_upload",
        input_payload={
            "session_id": str(session_id),
            "filename": filename,
            "page_id": str(page_id),
            "chunks_count": len(chunks),
        },
        worker=_process_documentation_worker,
        worker_kwargs={
            "session_id": str(session_id),
            "chunks": chunks,
            "filename": filename,
            "page_id": str(page_id),
            "app": app,
            "app_version": app_version,
        },
        initial_stage=JobStage.processing,
        initial_message=f"Processing {len(chunks)} chunks",
    )

    # Store job_id in session
    job_key = f"documentation_upload_{page_id}_job_id"
    SessionManager.update_session(session_id, {job_key: str(job_id)})

    # Return immediately with job info
    return {
        "message": "Documentation upload queued for processing",
        "sessionId": session_id,
        "jobId": job_id,
        "pageId": str(page_id),
        "chunksToProcess": len(chunks),
        "status": "queued",
    }


async def _process_documentation_worker(
    session_id: UUID,
    chunks: List[tuple[str, int]],
    filename: str,
    page_id: UUID,
    app: str,
    app_version: str,
    job_id: UUID,
) -> Dict[str, Any]:
    """Worker function to process documentation chunks."""
    try:
        # Get existing documentation items
        existing_docs = SessionManager.get_session_data(session_id, "documentationItems") or []

        # Set up progress tracking
        update_job_progress(
            job_id,
            stage=JobStage.processing,
            message=f"Processing {len(chunks)} chunks",
            total_documents=1,
            processed_documents=0,
        )

        # Process each chunk with LLM
        semaphore = asyncio.Semaphore(config.scrape_and_process.max_concurrent)

        logger.info("[Upload:Job] Processing %s chunks for session %s (job %s)", len(chunks), session_id, job_id)

        async def process_chunk(idx: int, chunk_data: tuple[str, int]) -> DocumentationItem:
            async with semaphore:
                chunk_text, chunk_length = chunk_data
                prompts = get_llm_chunk_process_prompt(chunk_text, filename, app, app_version)
                data = await get_llm_processed_chunk(prompts)

                return DocumentationItem(
                    source="upload",
                    page_id=page_id,
                    url=f"upload://{filename}",
                    summary=data.summary,
                    content=chunk_text,
                    metadata={
                        "filename": filename,
                        "chunk_number": idx,
                        "length": chunk_length,
                        "num_endpoints": data.num_endpoints,
                        "tags": data.tags,
                        "category": data.category,
                        "llm_tags": data.tags,
                        "llm_category": data.category,
                    },
                )

        # Process all chunks concurrently
        doc_items = await asyncio.gather(*[process_chunk(idx, chunk) for idx, chunk in enumerate(chunks)])

        # Add all chunks to existing docs
        for doc_item in doc_items:
            existing_docs.append(doc_item.model_dump(by_alias=True, mode="json"))

        SessionManager.update_session(session_id, {"documentationItems": existing_docs})

        # Mark document as processed
        increment_processed_documents(job_id, 1)

        logger.info(
            "[Upload:Job] Completed processing for session %s (job %s): generated %s chunks",
            session_id,
            job_id,
            len(doc_items),
        )

        return {
            "chunks_processed": len(doc_items),
            "page_id": page_id,
            "filename": filename,
        }
    except Exception as e:
        logger.exception(
            "[Upload:Job] Error processing documentation for session %s (job %s): %s", session_id, job_id, e
        )
        raise


@router.get("/{session_id}/documentation", summary="Get documentation from session")
async def get_documentation(session_id: UUID = Path(..., description="Session ID")) -> Dict[str, Any]:
    """
    Retrieve all documentation items stored in the session.
    Returns both scraped and uploaded documentation.
    """
    if not SessionManager.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    doc_items = SessionManager.get_session_data(session_id, "documentationItems")
    if not doc_items:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No documentation found in session {session_id}"
        )

    return {"sessionId": session_id, "documentationItems": doc_items, "count": len(doc_items)}


@router.put("/{session_id}/documentation", summary="Replace all documentation in session")
async def replace_documentation(
    session_id: UUID = Path(..., description="Session ID"),
    documentation: UploadFile = File(..., description="OpenAPI/Swagger YAML or JSON file"),
) -> Dict[str, Any]:
    """
    Replace all existing documentation in the session with a single uploaded file.
    This clears all previously scraped and uploaded documentation.
    Chunks and processes the documentation with LLM - returns immediately with job_id.
    """
    if not SessionManager.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    # Load app and app_version from session
    session_data = SessionManager.get_session_data(session_id) or {}
    discovery_input = session_data.get("discoveryInput", {})
    scrape_input = session_data.get("scrapeInput", {})

    # Try discoveryInput first, fallback to scrapeInput, then to "unknown"
    app = discovery_input.get("applicationName") or scrape_input.get("applicationName") or "unknown"
    app_version = discovery_input.get("applicationVersion") or scrape_input.get("applicationVersion") or "unknown"

    doc_text = (await documentation.read()).decode("utf-8", errors="ignore")
    filename = documentation.filename or "unknown"

    # Clear existing documentation first
    SessionManager.update_session(session_id, {"documentationItems": []})

    # Chunk the content
    logger.info("[Upload] Chunking documentation for session %s", session_id)
    chunks = split_text_with_token_overlap(
        doc_text, max_tokens=config.scrape_and_process.chunk_length, overlap_ratio=0.05
    )
    logger.info("[Upload] Generated %s chunks for uploaded document", len(chunks))

    page_id = uuid.uuid4()  # Single page_id for the entire uploaded file

    # Create job with the processing logic
    job_id = schedule_coroutine_job(
        job_type="documentation_upload",
        input_payload={
            "session_id": str(session_id),
            "filename": filename,
            "page_id": str(page_id),
            "chunks_count": len(chunks),
        },
        worker=_process_documentation_worker,
        worker_kwargs={
            "session_id": str(session_id),
            "chunks": chunks,
            "filename": filename,
            "page_id": str(page_id),
            "app": app,
            "app_version": app_version,
        },
        initial_stage=JobStage.processing,
        initial_message=f"Processing {len(chunks)} chunks",
    )

    # Store job_id in session
    job_key = f"documentation_upload_{page_id}_job_id"
    SessionManager.update_session(session_id, {job_key: str(job_id)})

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
) -> Dict[str, Any]:
    """
    Upload, chunk, and process documentation in the session with a specific page_id.
    Creates a job and queues it for processing - returns immediately with job_id.
    Each chunk becomes a separate DocumentationItem with source='upload' and the provided documentation_id as page_id.
    Application name and version are loaded from session's discoveryInput or scrapeInput.
    """
    if not SessionManager.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    # Load app and app_version from session
    session_data = SessionManager.get_session_data(session_id) or {}
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
    job_id = schedule_coroutine_job(
        job_type="documentation_upload",
        input_payload={
            "session_id": str(session_id),
            "filename": filename,
            "page_id": str(page_id),
            "chunks_count": len(chunks),
        },
        worker=_process_documentation_worker,
        worker_kwargs={
            "session_id": str(session_id),
            "chunks": chunks,
            "filename": filename,
            "page_id": str(page_id),
            "app": app,
            "app_version": app_version,
        },
        initial_stage=JobStage.processing,
        initial_message=f"Processing {len(chunks)} chunks",
    )

    # Store job_id in session
    job_key = f"documentation_upload_{page_id}_job_id"
    SessionManager.update_session(session_id, {job_key: str(job_id)})

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
async def delete_documentation(session_id: UUID = Path(..., description="Session ID")) -> dict:
    """
    Remove all documentation (both scraped and uploaded) from the session.
    """
    if not SessionManager.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    SessionManager.update_session(session_id, {"documentationItems": []})
    return {"message": "All documentation deleted successfully", "sessionId": session_id}


@router.head(
    "/{session_id}/documentation/{documentation_id}",
    summary="Checks if documentation item exists by UUID",
    status_code=204,
)
async def check_documentation_item(
    session_id: UUID = Path(..., description="Session ID"),
    documentation_id: UUID = Path(..., description="Documentation UUID"),
) -> None:
    """
    Checks a single documentation item from the session by its UUID.
    Returns 404 if the session or the documentation item is not found.
    """
    if not SessionManager.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    doc_items: list[dict] = SessionManager.get_session_data(session_id, "documentationItems") or []
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


@router.delete("/{session_id}/documentation/{documentation_id}", summary="Delete all documentation chunks by page_id")
async def delete_documentation_item(
    session_id: UUID = Path(..., description="Session ID"),
    documentation_id: UUID = Path(..., description="Documentation page_id (deletes all chunks with this page_id)"),
) -> Dict[str, Any]:
    """
    Delete all documentation chunks with the specified page_id from the session.
    Since uploaded documentation is chunked, this removes all chunks belonging to the same document.
    Returns 404 if the session or any documentation with that page_id is not found.
    """
    if not SessionManager.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    doc_items: list[dict] = SessionManager.get_session_data(session_id, "documentationItems") or []
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
    SessionManager.update_session(session_id, {"documentationItems": filtered_items})

    return {
        "message": f"Documentation deleted successfully ({deleted_count} chunk(s) removed)",
        "sessionId": session_id,
        "deletedPageId": str(documentation_id),
        "deletedChunks": deleted_count,
    }


# Helper Functions
async def get_session_documentation(session_id: UUID, documentation: UploadFile | None = None) -> list[dict]:
    """
    Helper to get all documentation items from session or uploaded file.
    Can be imported by other module routers.
    Returns list of documentation items with their UUIDs and content.
    """
    if not SessionManager.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    # If an upload is provided, read and store it in the session, then return it
    if documentation is not None:
        doc_text = (await documentation.read()).decode("utf-8", errors="ignore")

        # Store as a documentation item
        existing_docs: list[dict] = SessionManager.get_session_data(session_id, "documentationItems") or []
        doc_item = DocumentationItem(
            id=uuid.uuid4(),
            page_id=uuid.uuid4(),
            summary=None,
            source="upload",
            url=None,
            content=doc_text,
            metadata={"filename": documentation.filename or "unknown", "length": len(doc_text)},
        )
        doc_dict = doc_item.model_dump(by_alias=True)
        existing_docs.append(doc_dict)
        SessionManager.update_session(session_id, {"documentationItems": existing_docs})

        return [doc_dict]

    # Try to get all documentation items from session
    doc_items = SessionManager.get_session_data(session_id, "documentationItems")
    if doc_items and len(doc_items) > 0:
        return doc_items

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Session {session_id} has no stored documentation. Please upload documentation file or run scraper.",
    )
