# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Codegen relation endpoints."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database.config import get_db
from src.common.database.repositories.session_repository import SessionRepository
from src.common.schema import JobCreateResponse, JobStatusMultiDocResponse
from src.common.session.session import ensure_session_exists, resolve_session_job_id
from src.common.utils.status_response import build_multi_doc_status_response
from src.modules.codegen.orchestration import schedule_relation_job
from src.modules.codegen.persistence import store_relation_override
from src.modules.codegen.schema import GroovyCodePayload

router = APIRouter(tags=["Codegen: Relations"])


@router.post(
    "/{session_id}/relations/{relation_name}",
    response_model=JobCreateResponse,
    summary="Generate relation code",
)
async def generate_relation_code(
    session_id: UUID = Path(..., description="Session ID"),
    relation_name: str = Path(..., description="Relation name"),
    skip_cache: bool = Query(False, alias="skipCache", description="Whether to skip cached data for generation"),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate Groovy relation code.
    Loads relations from session automatically.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    job_id = await schedule_relation_job(
        repo=repo,
        session_id=session_id,
        relation_name=relation_name,
        skip_cache=skip_cache,
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/relations/{relation_name}",
    response_model=JobStatusMultiDocResponse,
    summary="Get relation code generation status",
)
async def get_relation_code_status(
    session_id: UUID = Path(..., description="Session ID"),
    relation_name: str = Path(..., description="Relation name"),
    jobId: Optional[UUID] = Query(None, description="Job ID (optional)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the status of relation code generation job.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    jobId = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key=f"{relation_name}CodeJobId",
        job_label="relation code",
        not_found_detail=f"No relation code job found for {relation_name} in session {session_id}",
    )

    return await build_multi_doc_status_response(jobId)


@router.put(
    "/{session_id}/relations/{relation_name}",
    summary="Override relation code",
)
async def override_relation_code(
    session_id: UUID = Path(..., description="Session ID"),
    relation_name: str = Path(..., description="Relation name"),
    relation_code: GroovyCodePayload = Body(..., description="Relation code as JSON"),
    db: AsyncSession = Depends(get_db),
):
    """
    Manually override the relation code.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)
    await store_relation_override(repo, session_id, relation_name, relation_code)

    return {
        "message": f"Relation code for {relation_name} overridden successfully",
        "sessionId": session_id,
        "relationName": relation_name,
    }
