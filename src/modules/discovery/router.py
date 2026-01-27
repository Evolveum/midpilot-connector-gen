# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ...common.database.config import get_db
from ...common.database.repositories.session_repository import SessionRepository
from ...common.jobs import schedule_coroutine_job
from ...common.schema import JobCreateResponse, JobStatusStageResponse
from ...common.session.session import ensure_session_exists, resolve_session_job_id
from ...common.status_response import build_stage_status_response
from . import service
from .schema import CandidateLinksInput

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
    db: AsyncSession = Depends(get_db),
):
    """
    Enqueue a job to discover candidate documentation URLs for the given application.
    The discovered URLs will be stored in the session.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    job_id = await schedule_coroutine_job(
        job_type="discovery.getCandidateLinks",
        input_payload=req.model_dump(by_alias=True),
        worker=service.fetch_candidate_links,
        worker_args=(req,),
        initial_stage="queue",
        initial_message="Queued candidate links discovery",
        session_id=session_id,
        session_result_key="discoveryOutput",
    )

    await repo.update_session(
        session_id, {"discoveryJobId": str(job_id), "discoveryInput": req.model_dump(by_alias=True)}
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
    db: AsyncSession = Depends(get_db),
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
