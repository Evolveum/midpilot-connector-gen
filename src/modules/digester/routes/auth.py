# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Digester authentication endpoints."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database.config import get_db
from src.common.database.repositories.session_repository import SessionRepository
from src.common.schema import JobCreateResponse, JobStatusMultiDocResponse
from src.common.session.session import ensure_session_exists, resolve_session_job_id
from src.common.utils.status_response import build_typed_job_status_response
from src.modules.digester import orchestration
from src.modules.digester.schemas import AuthInfo, AuthResponse

router = APIRouter(tags=["Digester: Auth"])


@router.post(
    "/{session_id}/auth",
    response_model=JobCreateResponse,
    summary="Extract authentication information",
)
async def extract_auth(
    session_id: UUID = Path(..., description="Session ID"),
    skip_cache: bool = Query(False, alias="skipCache", description="Whether to skip cached data"),
    db: AsyncSession = Depends(get_db),
):
    """
    Extract authentication information from documentation.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    job_id = await orchestration.schedule_auth_extraction(
        repo=repo,
        session_id=session_id,
        skip_cache=skip_cache,
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/auth",
    response_model=JobStatusMultiDocResponse,
    summary="Get auth extraction status",
)
async def get_auth_status(
    session_id: UUID = Path(..., description="Session ID"),
    jobId: Optional[UUID] = Query(None, description="Job ID (optional)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the status of auth extraction job.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    resolved_job_id = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key="authJobId",
        job_label="auth",
    )

    return await build_typed_job_status_response(resolved_job_id, AuthResponse[AuthInfo])
