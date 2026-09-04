# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Codegen ConnID endpoints."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.responses import build_stage_status_response
from src.core.db import DbSession
from src.database.repositories.session_repository import SessionRepository
from src.documents.normalize import normalize_object_class_name
from src.jobs.schema import JobCreateResponse, JobStatusStageResponse
from src.modules.codegen.orchestration import schedule_connid_job
from src.modules.codegen.persistence import store_object_class_output_override
from src.modules.codegen.schema import CodegenRepairContext, GroovyCodePayload
from src.session.access import ensure_session_exists, resolve_session_job_id

router = APIRouter(tags=["Codegen: ConnID"])


@router.post(
    "/{session_id}/classes/{object_class}/connid",
    response_model=JobCreateResponse,
    summary="Generate ConnID for object class",
)
async def generate_connid(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    skip_cache: bool = Query(False, alias="skipCache", description="Whether to skip cached data for generation"),
    db: AsyncSession = DbSession,
    codegen_input: Optional[CodegenRepairContext] = None,
):
    """
    Generate ConnID Groovy code from attributes.
    Loads attributes from session automatically.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    job_id = await schedule_connid_job(
        repo=repo,
        session_id=session_id,
        object_class=object_class,
        skip_cache=skip_cache,
        codegen_input=codegen_input,
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/classes/{object_class}/connid",
    response_model=JobStatusStageResponse,
    summary="Get ConnID generation status",
    response_model_exclude_none=True,
)
async def get_connid_status(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    jobId: Optional[UUID] = Query(None, description="Job ID (optional)"),
    db: AsyncSession = DbSession,
):
    """
    Get the status of ConnID generation job.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    jobId = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key=f"{object_class}ConnidJobId",
        job_label="ConnID",
        not_found_detail=f"No ConnID job found for {object_class} in session {session_id}",
    )

    return await build_stage_status_response(jobId)


@router.put(
    "/{session_id}/classes/{object_class}/connid",
    summary="Override ConnID",
)
async def override_connid(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    connid: GroovyCodePayload = Body(..., description="ConnID code as JSON"),
    db: AsyncSession = DbSession,
):
    """
    Manually override the ConnID for an object class.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)
    await store_object_class_output_override(repo, session_id, object_class, "Connid", connid)

    return {
        "message": f"ConnID for {object_class} overridden successfully",
        "sessionId": session_id,
        "objectClass": object_class,
    }
