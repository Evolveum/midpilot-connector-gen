# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.responses import build_stage_status_response
from src.core.db import DbSession
from src.database.repositories.session_repository import SessionRepository
from src.jobs.schema import JobCreateResponse, JobStatusStageResponse
from src.modules.discovery import orchestration
from src.modules.discovery.schema import CandidateLinksInput
from src.session.access import ensure_session_exists, resolve_session_job_id

router = APIRouter()


# Discovery Operations
@router.post(
    "/{session_id}/discovery",
    response_model=JobCreateResponse,
    summary="Discover candidate documentation URLs",
)
async def discover_candidate_links(
    req: CandidateLinksInput,
    session_id: UUID = Path(..., description="Session ID"),
    db: AsyncSession = DbSession,
):
    """
    Enqueue a job to discover candidate documentation URLs for the given application.
    The discovered URLs will be stored in the session.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    job_id = await orchestration.schedule_candidate_link_discovery(
        repo=repo,
        session_id=session_id,
        request=req,
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/discovery",
    response_model=JobStatusStageResponse,
    summary="Get discovery job status",
    response_model_exclude_none=True,
)
async def get_discovery_status(
    session_id: UUID = Path(..., description="Session ID"),
    jobId: Optional[UUID] = Query(None, description="Job ID (optional)"),
    db: AsyncSession = DbSession,
):
    """
    Get the status of candidate links discovery job.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    jobId = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key="discoveryJobId",
        job_label="discovery",
    )
    return await build_stage_status_response(jobId)
