# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Digester relation endpoints."""

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
from src.modules.digester.schemas import RelationsResponse

router = APIRouter(tags=["Digester: Relations"])


@router.post(
    "/{session_id}/relations",
    response_model=JobCreateResponse,
    summary="Extract relations between object classes",
)
async def extract_relations(
    session_id: UUID = Path(..., description="Session ID"),
    skip_cache: bool = Query(False, alias="skipCache", description="Whether to skip cached data"),
    db: AsyncSession = Depends(get_db),
):
    """
    Extract relations between object classes from documentation.
    Loads object classes from session.

    NOTE: We dont need to await documentation here, as it should have already been awaited during object class extraction.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    job_id = await orchestration.schedule_relations_extraction(
        db=db,
        repo=repo,
        session_id=session_id,
        skip_cache=skip_cache,
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/relations",
    response_model=JobStatusMultiDocResponse,
    summary="Get relations extraction status",
)
async def get_relations_status(
    session_id: UUID = Path(..., description="Session ID"),
    jobId: Optional[UUID] = Query(None, description="Job ID (optional)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the status of relations extraction job.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    resolved_job_id = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key="relationsJobId",
        job_label="relations",
    )

    return await build_typed_job_status_response(resolved_job_id, RelationsResponse)


@router.put(
    "/{session_id}/relations",
    summary="Override relations data",
)
async def override_relations(
    session_id: UUID = Path(..., description="Session ID"),
    relations: RelationsResponse = Body(..., description="Relations data as JSON"),
    db: AsyncSession = Depends(get_db),
):
    """
    Manually override the relations data.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    await results.store_relations_override(repo, session_id, relations.model_dump(by_alias=True, mode="json"))

    return {"message": "Relations overridden successfully", "sessionId": session_id}
