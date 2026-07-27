# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import get_db
from src.database.repositories.session_repository import SessionRepository
from src.jobs import get_job_status
from src.jobs.schema import JobCreateResponse, JobStatusIterationResponse
from src.modules.scrape import orchestration
from src.modules.scrape.schema import ScrapeRequest
from src.session.access import ensure_session_exists, resolve_session_job_id
from src.shared.enums import JobStatus

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

    job_id = await orchestration.schedule_scrape_documentation(
        repo=repo,
        session_id=session_id,
        request=req,
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

    jobId = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key="scrapeJobId",
        job_label="scrape",
    )

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
