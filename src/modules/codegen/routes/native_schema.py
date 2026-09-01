# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Codegen native-schema endpoints."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.responses import build_stage_status_response
from src.core.db import get_db
from src.database.repositories.session_repository import SessionRepository
from src.documents.normalize import normalize_object_class_name
from src.jobs.schema import JobCreateResponse, JobStatusStageResponse
from src.modules.codegen.orchestration import schedule_native_schema_job
from src.modules.codegen.persistence import store_object_class_output_override
from src.modules.codegen.schema import CodegenRepairContext, GroovyCodePayload
from src.session.access import ensure_session_exists, resolve_session_job_id
from src.shared.enums import ApiType

router = APIRouter(tags=["Codegen: Native Schema"])


@router.post(
    "/{session_id}/classes/{object_class}/native-schema",
    response_model=JobCreateResponse,
    summary="Generate native schema for object class",
)
async def generate_native_schema(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    skip_cache: bool = Query(False, alias="skipCache", description="Whether to skip cached data for generation"),
    api_type: Optional[ApiType] = Query(
        None,
        alias="apiType",
        description="Override the API protocol (REST/SCIM/SQL); falls back to the detected apiType when omitted.",
    ),
    db: AsyncSession = Depends(get_db, scope="function"),
    codegen_input: Optional[CodegenRepairContext] = None,
):
    """
    Generate native Groovy schema from attributes.
    Loads attributes from session automatically.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    job_id = await schedule_native_schema_job(
        repo=repo,
        session_id=session_id,
        object_class=object_class,
        api_type=api_type,
        skip_cache=skip_cache,
        codegen_input=codegen_input,
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/classes/{object_class}/native-schema",
    response_model=JobStatusStageResponse,
    summary="Get native schema generation status",
    response_model_exclude_none=True,
)
async def get_native_schema_status(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    jobId: Optional[UUID] = Query(None, description="Job ID (optional)"),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Get the status of native schema generation job.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    jobId = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key=f"{object_class}NativeSchemaJobId",
        job_label="native schema",
        not_found_detail=f"No native schema job found for {object_class} in session {session_id}",
    )

    return await build_stage_status_response(jobId)


@router.put(
    "/{session_id}/classes/{object_class}/native-schema",
    summary="Override native schema",
)
async def override_native_schema(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    native_schema: GroovyCodePayload = Body(..., description="Native schema code as JSON"),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Manually override the native schema for an object class.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)
    await store_object_class_output_override(repo, session_id, object_class, "NativeSchema", native_schema)

    return {
        "message": f"Native schema for {object_class} overridden successfully",
        "sessionId": session_id,
        "objectClass": object_class,
    }
