# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Codegen search endpoints."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.responses import build_multi_doc_status_response
from src.core.db import get_db
from src.database.repositories.session_repository import SessionRepository
from src.documents.normalize import normalize_object_class_name
from src.jobs.schema import JobCreateResponse, JobStatusMultiDocResponse
from src.modules.codegen import generation
from src.modules.codegen.enums import SearchIntent, build_search_operation_key
from src.modules.codegen.orchestration import schedule_operation_job
from src.modules.codegen.persistence import store_search_override
from src.modules.codegen.schema import CodegenOperationInput, GroovyCodePayload
from src.session.access import ensure_session_exists, resolve_session_job_id
from src.shared.enums import ApiType

router = APIRouter(tags=["Codegen: Search"])


@router.post(
    "/{session_id}/classes/{object_class}/search/{intent}",
    response_model=JobCreateResponse,
    summary="Generate search code for object class",
)
async def generate_search(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    intent: SearchIntent = Path(..., description="Intent"),
    skip_cache: bool = Query(False, alias="skipCache", description="Whether to skip cached data for generation"),
    api_type: Optional[ApiType] = Query(
        None,
        alias="apiType",
        description="Override the API protocol (REST/SCIM/SQL); falls back to the detected apiType when omitted.",
    ),
    db: AsyncSession = Depends(get_db, scope="function"),
    codegen_input: Optional[CodegenOperationInput] = None,
):
    """
    Generate Groovy search code for the given object class.
    Loads attributes and endpoints from session automatically.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    operation_key = build_search_operation_key(object_class, intent)
    job_id = await schedule_operation_job(
        repo=repo,
        session_id=session_id,
        object_class=object_class,
        skip_cache=skip_cache,
        api_type=api_type,
        codegen_input=codegen_input,
        key_prefix=operation_key,
        job_type="codegen.getSearch",
        worker=generation.generate_search_code,
        extra_job_input={"intent": intent},
        extra_worker_kwargs={"intent": intent},
        extra_session_input={"intent": intent},
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/classes/{object_class}/search/{intent}",
    response_model=JobStatusMultiDocResponse,
    summary="Get search generation status",
)
async def get_search_status(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    intent: SearchIntent = Path(..., description="Intent"),
    jobId: Optional[UUID] = Query(None, description="Job ID (optional)"),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Get the status of search code generation job.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    operation_key = build_search_operation_key(object_class, intent)

    jobId = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key=f"{operation_key}JobId",
        job_label="search",
        not_found_detail=f"No search job found for {object_class} intent={intent} in session {session_id}",
    )

    return await build_multi_doc_status_response(jobId)


# Maybe in the future add to the cache?
@router.put(
    "/{session_id}/classes/{object_class}/search/{intent}",
    summary="Override search code",
)
async def override_search(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    intent: SearchIntent = Path(..., description="Intent"),
    search_code: GroovyCodePayload = Body(..., description="Search code as JSON"),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Manually override the search code for an object class.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)
    await store_search_override(repo, session_id, object_class, intent, search_code)

    return {
        "message": f"Search code for {object_class} overridden successfully",
        "sessionId": session_id,
        "objectClass": object_class,
    }
