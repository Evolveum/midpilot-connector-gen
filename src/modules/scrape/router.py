# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ...common.database.config import get_db
from ...common.database.repositories.session_repository import SessionRepository
from ...common.enums import JobStatus
from ...common.jobs import get_job_status, schedule_coroutine_job
from ...common.schema import JobCreateResponse, JobStatusIterationResponse
from ...common.session.session import ensure_session_exists, resolve_session_job_id
from . import service
from .schema import ScrapeRequest

router = APIRouter()


async def _resolve_scrape_request(
    req: ScrapeRequest,
    repo: SessionRepository,
    session_id: UUID,
) -> ScrapeRequest:
    explicit_input = req.model_dump(by_alias=True, exclude_unset=True)
    explicit_version = explicit_input.get("applicationVersion")

    if isinstance(explicit_version, str) and explicit_version.strip():
        return req.model_copy(update={"application_version": explicit_version.strip()})

    discovery_input = await repo.get_session_data(session_id, "discoveryInput") or {}
    if isinstance(discovery_input, dict):
        discovery_version = str(discovery_input.get("applicationVersion") or "").strip()
        if discovery_version:
            return req.model_copy(update={"application_version": discovery_version})

    current_version = str(req.application_version or "").strip()
    if current_version:
        return req.model_copy(update={"application_version": current_version})

    return req.model_copy(update={"application_version": "current"})


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
    resolved_req = await _resolve_scrape_request(req, repo, session_id)

    job_id = await schedule_coroutine_job(
        job_type="scrape.getRelevantDocumentation",
        input_payload=resolved_req.model_dump(by_alias=True),
        worker=service.fetch_relevant_documentation,
        worker_args=(resolved_req, session_id),
        initial_stage="queue",
        initial_message="Queued scraping job",
        session_id=session_id,
        session_result_key="scrapeOutput",
    )

    await repo.update_session(
        session_id,
        {
            "scrapeJobId": str(job_id),
            "scrapeInput": resolved_req.model_dump(by_alias=True),
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
