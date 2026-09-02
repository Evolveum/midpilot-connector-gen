# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Digester connectivity-endpoint endpoints."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.responses import build_typed_job_status_response
from src.core.db import get_db
from src.database.repositories.session_repository import SessionRepository
from src.jobs.schema import JobCreateResponse, JobStatusMultiDocResponse
from src.modules.digester import orchestration, results
from src.modules.digester.schemas import ConnectivityEndpointResponse
from src.session.access import ensure_session_exists, resolve_session_job_id

router = APIRouter(tags=["Digester: Connectivity Endpoint"])


@router.post(
    "/{session_id}/connectivity-endpoint",
    response_model=JobCreateResponse,
    summary="Extract connectivity test endpoint",
)
async def extract_connectivity_endpoint(
    session_id: UUID = Path(..., description="Session ID"),
    skip_cache: bool = Query(False, alias="skipCache", description="Whether to skip cached data"),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Extract one documented endpoint suitable for testing connectivity to the target application.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    job_id = await orchestration.schedule_connectivity_endpoint_extraction(
        repo=repo,
        session_id=session_id,
        skip_cache=skip_cache,
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/connectivity-endpoint",
    response_model=JobStatusMultiDocResponse,
    summary="Get connectivity endpoint extraction status",
)
async def get_connectivity_endpoint_status(
    session_id: UUID = Path(..., description="Session ID"),
    jobId: Optional[UUID] = Query(None, description="Job ID (optional)"),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Get the status of connectivity endpoint extraction job.
    Returns current session data when the job has finished, including manual overrides.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    resolved_job_id = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key="connectivityEndpointJobId",
        job_label="connectivity endpoint",
    )

    response = await build_typed_job_status_response(resolved_job_id, ConnectivityEndpointResponse)
    return await results.refresh_connectivity_endpoint_status(db, repo, response, session_id)


@router.put(
    "/{session_id}/connectivity-endpoint",
    summary="Override connectivity test endpoint",
)
async def override_connectivity_endpoint(
    session_id: UUID = Path(..., description="Session ID"),
    connectivity_endpoint: ConnectivityEndpointResponse = Body(..., description="Connectivity endpoint payload"),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Manually override the selected connectivity endpoint in the session.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    payload = connectivity_endpoint.model_dump(by_alias=True, mode="json")
    await results.store_connectivity_endpoint_override(db, repo, session_id, payload)

    return {"message": "Connectivity endpoint overridden successfully", "sessionId": session_id}
