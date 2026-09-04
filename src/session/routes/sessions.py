# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Session lifecycle endpoints (create, inspect, delete)."""

import logging
from typing import Any, Dict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.context import AuthContext
from src.auth.dependencies import get_auth_context
from src.core.db import DbSession
from src.database.repositories.job_repository import JobRepository
from src.database.repositories.session_repository import SessionRepository
from src.session.access import ensure_session_exists
from src.session.errors import SessionAlreadyExistsError, SessionNotFoundError
from src.session.schema import SessionCreateResponse

logger = logging.getLogger(__name__)

router = APIRouter()


# GET Endpoints
@router.get(
    "/{session_id}",
    summary="Get session summary",
)
async def get_session_summary(
    session_id: UUID = Path(..., description="Session ID"), db: AsyncSession = DbSession
) -> Dict[str, Any]:
    """
    Retrieve a top-level summary of session data by session ID.
    """
    repo = SessionRepository(db)
    session = await repo.get_session(session_id)
    if session is None:
        raise SessionNotFoundError(session_id)

    return {
        "sessionId": session["sessionId"],
        "createdAt": session["createdAt"],
        "updatedAt": session["updatedAt"],
        "message": "Session returned successfully.",
    }


@router.get(
    "/{session_id}/jobs",
    summary="List all jobs in session",
)
async def list_session_jobs(
    session_id: UUID = Path(..., description="Session ID"), db: AsyncSession = DbSession
) -> Dict[str, Any]:
    """
    List all jobs associated with this session.
    Returns job IDs and their current status.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    job_repo = JobRepository(db)
    jobs = await job_repo.get_jobs_by_session(session_id)

    return {"sessionId": session_id, "jobs": jobs}


# HEAD Endpoints
@router.head("/{session_id}", summary="Check if session exists", status_code=204)
async def check_session_exists(
    session_id: UUID = Path(..., description="Session ID"), db: AsyncSession = DbSession
) -> None:
    """
    Check if a session exists by session ID.
    Returns 204 No Content if exists, 404 Not Found if not.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)


# POST Endpoints
@router.post(
    "",
    response_model=SessionCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new session",
)
async def create_session(
    db: AsyncSession = DbSession,
    auth: AuthContext = Depends(get_auth_context),
) -> SessionCreateResponse:
    """
    Create a new session and return the session ID.
    The session is owned by the API key that created it (if any).
    """
    repo = SessionRepository(db)
    try:
        session_id = await repo.create_session(api_key_id=auth.api_key_id)
    except Exception as e:
        logger.error("Failed to create session: %s", e)
        raise HTTPException(status_code=500, detail="Unable to create session")
    return SessionCreateResponse(
        sessionId=session_id,
        message="Session created successfully. Use this session_id in subsequent requests.",
    )


@router.post(
    "/{session_id}",
    response_model=SessionCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new session with provided Session ID",
)
async def create_session_with_id(
    session_id: UUID = Path(..., description="Session ID"),
    db: AsyncSession = DbSession,
    auth: AuthContext = Depends(get_auth_context),
) -> SessionCreateResponse:
    """
    Create a new session using the provided session ID.
    The session is owned by the API key that created it (if any).
    Returns 409 if the session already exists.
    """
    repo = SessionRepository(db)
    if await repo.session_exists(session_id):
        logger.error("Cannot create session - session already exists: %s", session_id)
        raise SessionAlreadyExistsError(session_id)

    try:
        created_id = await repo.create_session_with_id(session_id, api_key_id=auth.api_key_id)
    except ValueError as e:
        logger.error("Failed to create session with ID %s: %s", session_id, e)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except Exception as e:
        logger.error("Failed to create session with ID %s: %s", session_id, e)
        raise HTTPException(status_code=500, detail="Unable to create session")

    return SessionCreateResponse(
        sessionId=created_id,
        message="Session created successfully with provided ID.",
    )


# DELETE Endpoints
@router.delete(
    "/{session_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a session",
)
async def delete_session(
    session_id: UUID = Path(..., description="Session ID"), db: AsyncSession = DbSession
) -> Dict[str, Any]:
    """
    Delete a session and all associated data.
    """
    repo = SessionRepository(db)
    success = await repo.delete_session(session_id)
    if not success:
        raise SessionNotFoundError(session_id)
    return {"message": "Session deleted successfully", "sessionId": session_id}
