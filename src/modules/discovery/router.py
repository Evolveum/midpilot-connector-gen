# Copyright (c) 2025 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Discovery endpoints for V2 API (session-centric).
All discovery operations are nested under sessions.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi import Path as PathParam

from ...common.jobs import schedule_coroutine_job
from ...common.schema import JobCreateResponse, JobStatusStageResponse
from ...common.session.session import SessionManager
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
    session_id: UUID = PathParam(..., description="Session ID"),
):
    """
    Enqueue a job to discover candidate documentation URLs for the given application.
    The discovered URLs will be stored in the session.
    """
    if not SessionManager.session_exists(session_id):
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    job_id = schedule_coroutine_job(
        job_type="discovery.getCandidateLinks",
        input_payload=req.model_dump(by_alias=True),
        worker=service.fetch_candidate_links,
        worker_args=(req,),
        initial_stage="queue",
        initial_message="Queued candidate links discovery",
        session_id=session_id,
        session_result_key="discoveryOutput",
    )

    SessionManager.update_session(
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
    session_id: UUID = PathParam(..., description="Session ID"),
    jobId: Optional[UUID] = Query(None, description="Job ID (optional)"),
):
    """
    Get the status of candidate links discovery job.
    """
    if not SessionManager.session_exists(session_id):
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    if not jobId:
        job_id_str = SessionManager.get_session_data(session_id, "discoveryJobId")
        if not job_id_str:
            raise HTTPException(status_code=404, detail=f"No discovery job found in session {session_id}")
        return build_stage_status_response(job_id_str)

    return build_stage_status_response(jobId)
