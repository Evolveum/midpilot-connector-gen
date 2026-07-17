# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Digester metadata endpoints."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database.config import get_db
from src.common.database.repositories.session_repository import SessionRepository
from src.common.schema import JobCreateResponse, JobStatusMultiDocResponse
from src.common.session.session import ensure_session_exists, resolve_session_job_id
from src.common.utils.status_response import build_typed_job_status_response
from src.modules.digester import orchestration, results
from src.modules.digester.schemas import InfoResponse

router = APIRouter(tags=["Digester: Metadata"])


@router.post(
    "/{session_id}/metadata",
    response_model=JobCreateResponse,
    summary="Extract metadata information",
)
async def extract_metadata(
    session_id: UUID = Path(..., description="Session ID"),
    skip_cache: bool = Query(False, alias="skipCache", description="Whether to skip cached data"),
    db: AsyncSession = Depends(get_db),
):
    """
    Extract API metadata from documentation.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    job_id = await orchestration.schedule_metadata_extraction(
        repo=repo,
        session_id=session_id,
        skip_cache=skip_cache,
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/metadata",
    response_model=JobStatusMultiDocResponse,
    summary="Get metadata extraction status",
)
async def get_metadata_status(
    session_id: UUID = Path(..., description="Session ID"),
    jobId: Optional[UUID] = Query(None, description="Job ID (optional)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the status of metadata extraction job.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    resolved_job_id = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key="metadataJobId",
        job_label="metadata",
    )

    return await build_typed_job_status_response(resolved_job_id, InfoResponse)


@router.put(
    "/{session_id}/metadata",
    summary="Restore metadata information",
)
async def restore_metadata(
    session_id: UUID = Path(..., description="Session ID"),
    metadata: InfoResponse = Body(..., description="Info metadata payload as JSON"),
    db: AsyncSession = Depends(get_db),
):
    """
    Restore metadataOutput in session from provided infoMetadata payload.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)
    await results.store_metadata_output(repo, session_id, metadata.model_dump(by_alias=True))

    return {"message": "Metadata updated successfully", "sessionId": session_id}
