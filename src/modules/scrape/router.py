# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...common.database.config import get_db
from ...common.database.repositories.session_repository import SessionRepository
from ...common.enums import JobStatus
from ...common.jobs import get_job_status, schedule_coroutine_job
from ...common.schema import JobCreateResponse, JobStatusIterationResponse
from ...common.session.session import ensure_session_exists
from . import service
from .schema import ScrapeRequest

router = APIRouter()


# Scrape Operations
@router.post(
    "/{session_id}/scrape",
    response_model=JobCreateResponse,
    summary="Scrape documentation from URLs",
)
async def scrape_documentation(
    req: ScrapeRequest,
    session_id: UUID = Path(..., description="Session ID"),
    db: AsyncSession = Depends(get_db),
):
    """
    Enqueue a job to scrape documentation from provided URLs.
    The scraped documentation will be stored in the session.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    job_id = await schedule_coroutine_job(
        job_type="scrape.getRelevantDocumentation",
        input_payload=req.model_dump(by_alias=True),
        worker=service.fetch_relevant_documentation,
        worker_args=(req, session_id),
        initial_stage="queue",
        initial_message="Queued scraping job",
        session_id=session_id,
        session_result_key="scrapeOutput",
    )

    await repo.update_session(
        session_id,
        {
            "scrapeJobId": str(job_id),
            "scrapeInput": req.model_dump(by_alias=True),
        },
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/scrape",
    response_model=JobStatusIterationResponse,
    summary="Get scrape job status",
    response_model_exclude_none=True,
)
async def get_scrape_status(
    session_id: UUID = Path(..., description="Session ID"),
    jobId: Optional[UUID] = Query(None, description="Job ID (optional)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the status of documentation scraping job.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    if not jobId:
        job_id_str = await repo.get_session_data(session_id, "scrapeJobId")
        if not job_id_str:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"No scrape job found in session {session_id}"
            )
        jobId = UUID(job_id_str)

    job_status = await get_job_status(jobId)
    raw_status = job_status.get("status", JobStatus.not_found.value)
    enum_status = JobStatus(raw_status)

    return JobStatusIterationResponse(
        jobId=job_status.get("jobId", jobId),
        status=enum_status,
        createdAt=job_status.get("createdAt"),
        startedAt=job_status.get("startedAt"),
        updatedAt=job_status.get("updatedAt"),
        progress=job_status.get("progress"),
        result=job_status.get("result"),
        errors=job_status.get("errors"),
    )
