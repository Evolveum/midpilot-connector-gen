# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Codegen create/update/delete endpoints (they share the per-object-class operation flow)."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database.config import get_db
from src.common.database.repositories.session_repository import SessionRepository
from src.common.enums import ApiType
from src.common.schema import JobCreateResponse, JobStatusMultiDocResponse
from src.common.session.session import ensure_session_exists, resolve_session_job_id
from src.common.utils.normalize import normalize_object_class_name
from src.common.utils.status_response import build_multi_doc_status_response
from src.modules.codegen import generation
from src.modules.codegen.orchestration import schedule_operation_job
from src.modules.codegen.persistence import store_object_class_output_override
from src.modules.codegen.schema import CodegenOperationInput, GroovyCodePayload

router = APIRouter()


# Codegen Operations - Create
@router.post(
    "/{session_id}/classes/{object_class}/create",
    tags=["CodeGen: Create"],
    response_model=JobCreateResponse,
    summary="Generate create code for object class",
)
async def generate_create(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    skip_cache: bool = Query(False, alias="skipCache", description="Whether to skip cached data for generation"),
    api_type: Optional[ApiType] = Query(
        None,
        alias="apiType",
        description="Override the API protocol (REST/SCIM/SQL); falls back to the detected apiType when omitted.",
    ),
    db: AsyncSession = Depends(get_db),
    codegen_input: Optional[CodegenOperationInput] = None,
):
    """
    Generate Groovy create code for the given object class.
    Loads attributes and endpoints from session automatically.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    job_id = await schedule_operation_job(
        repo=repo,
        session_id=session_id,
        object_class=object_class,
        skip_cache=skip_cache,
        api_type=api_type,
        codegen_input=codegen_input,
        key_prefix=f"{object_class}Create",
        job_type="codegen.getCreate",
        worker=generation.generate_create_code,
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/classes/{object_class}/create",
    tags=["CodeGen: Create"],
    response_model=JobStatusMultiDocResponse,
    summary="Get create generation status",
)
async def get_create_status(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    jobId: Optional[UUID] = Query(None, description="Job ID (optional)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the status of create code generation job.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    jobId = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key=f"{object_class}CreateJobId",
        job_label="create",
        not_found_detail=f"No create job found for {object_class} in session {session_id}",
    )

    return await build_multi_doc_status_response(jobId)


@router.put(
    "/{session_id}/classes/{object_class}/create",
    tags=["CodeGen: Create"],
    summary="Override create code",
)
async def override_create(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    create_code: GroovyCodePayload = Body(..., description="Create code as JSON"),
    db: AsyncSession = Depends(get_db),
):
    """
    Manually override the create code for an object class.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)
    await store_object_class_output_override(repo, session_id, object_class, "Create", create_code)

    return {
        "message": f"Create code for {object_class} overridden successfully",
        "sessionId": session_id,
        "objectClass": object_class,
    }


# Codegen Operations - Update
@router.post(
    "/{session_id}/classes/{object_class}/update",
    tags=["CodeGen: Update"],
    response_model=JobCreateResponse,
    summary="Generate update code for object class",
)
async def generate_update(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    skip_cache: bool = Query(False, alias="skipCache", description="Whether to skip cached data for generation"),
    api_type: Optional[ApiType] = Query(
        None,
        alias="apiType",
        description="Override the API protocol (REST/SCIM/SQL); falls back to the detected apiType when omitted.",
    ),
    db: AsyncSession = Depends(get_db),
    codegen_input: Optional[CodegenOperationInput] = None,
):
    """
    Generate Groovy update code for the given object class.
    Loads attributes and endpoints from session automatically.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    job_id = await schedule_operation_job(
        repo=repo,
        session_id=session_id,
        object_class=object_class,
        skip_cache=skip_cache,
        api_type=api_type,
        codegen_input=codegen_input,
        key_prefix=f"{object_class}Update",
        job_type="codegen.getUpdate",
        worker=generation.generate_update_code,
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/classes/{object_class}/update",
    tags=["CodeGen: Update"],
    response_model=JobStatusMultiDocResponse,
    summary="Get update generation status",
)
async def get_update_status(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    jobId: Optional[UUID] = Query(None, description="Job ID (optional)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the status of update code generation job.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    jobId = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key=f"{object_class}UpdateJobId",
        job_label="update",
        not_found_detail=f"No update job found for {object_class} in session {session_id}",
    )

    return await build_multi_doc_status_response(jobId)


@router.put(
    "/{session_id}/classes/{object_class}/update",
    tags=["CodeGen: Update"],
    summary="Override update code",
)
async def override_update(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    update_code: GroovyCodePayload = Body(..., description="Update code as JSON"),
    db: AsyncSession = Depends(get_db),
):
    """
    Manually override the update code for an object class.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)
    await store_object_class_output_override(repo, session_id, object_class, "Update", update_code)

    return {
        "message": f"Update code for {object_class} overridden successfully",
        "sessionId": session_id,
        "objectClass": object_class,
    }


# Codegen Operations - Delete
@router.post(
    "/{session_id}/classes/{object_class}/delete",
    tags=["CodeGen: Delete"],
    response_model=JobCreateResponse,
    summary="Generate delete code for object class",
)
async def generate_delete(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    skip_cache: bool = Query(False, alias="skipCache", description="Whether to skip cached data for generation"),
    api_type: Optional[ApiType] = Query(
        None,
        alias="apiType",
        description="Override the API protocol (REST/SCIM/SQL); falls back to the detected apiType when omitted.",
    ),
    db: AsyncSession = Depends(get_db),
    codegen_input: Optional[CodegenOperationInput] = None,
):
    """
    Generate Groovy delete code for the given object class.
    Loads attributes and endpoints from session automatically.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    job_id = await schedule_operation_job(
        repo=repo,
        session_id=session_id,
        object_class=object_class,
        skip_cache=skip_cache,
        api_type=api_type,
        codegen_input=codegen_input,
        key_prefix=f"{object_class}Delete",
        job_type="codegen.getDelete",
        worker=generation.generate_delete_code,
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/classes/{object_class}/delete",
    tags=["CodeGen: Delete"],
    response_model=JobStatusMultiDocResponse,
    summary="Get delete generation status",
)
async def get_delete_status(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    jobId: Optional[UUID] = Query(None, description="Job ID (optional)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the status of delete code generation job.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    jobId = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key=f"{object_class}DeleteJobId",
        job_label="delete",
        not_found_detail=f"No delete job found for {object_class} in session {session_id}",
    )

    return await build_multi_doc_status_response(jobId)


@router.put(
    "/{session_id}/classes/{object_class}/delete",
    tags=["CodeGen: Delete"],
    summary="Override delete code",
)
async def override_delete(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    delete_code: GroovyCodePayload = Body(..., description="Delete code as JSON"),
    db: AsyncSession = Depends(get_db),
):
    """
    Manually override the delete code for an object class.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)
    await store_object_class_output_override(repo, session_id, object_class, "Delete", delete_code)

    return {
        "message": f"Delete code for {object_class} overridden successfully",
        "sessionId": session_id,
        "objectClass": object_class,
    }
