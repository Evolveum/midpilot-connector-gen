# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Digester object-class endpoint endpoints."""

from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.responses import build_typed_job_status_response
from src.core.db import get_db
from src.database.repositories.session_repository import SessionRepository
from src.documents.normalize import normalize_object_class_name
from src.jobs.schema import JobCreateResponse, JobStatusMultiDocResponse
from src.modules.digester import orchestration, results
from src.modules.digester.schemas import EndpointResponse
from src.session.access import ensure_session_exists, resolve_session_job_id
from src.shared.enums import ApiType

router = APIRouter(tags=["Digester: Endpoints"])


@router.post(
    "/{session_id}/classes/{object_class}/endpoints",
    response_model=JobCreateResponse,
    summary="Extract endpoints for object class",
)
async def extract_class_endpoints(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    skip_cache: bool = Query(False, alias="skipCache", description="Whether to skip cached data"),
    api_type: Optional[ApiType] = Query(
        None,
        alias="apiType",
        description="Override the API protocol (REST/SCIM/SQL); falls back to the detected apiType when omitted.",
    ),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Extract API endpoints for a specific object class.
    Automatically loads base API URL from session metadata if available.
    Updates both {object_class}EndpointsOutput and the endpoints field in the specific object class.
    Only processes chunks that are relevant to the object class (from relevantDocumentations).

    NOTE: We dont need to await documentation here, as it should have already been awaited during object class extraction.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    job_id = await orchestration.schedule_endpoint_extraction(
        db=db,
        repo=repo,
        session_id=session_id,
        object_class=object_class,
        skip_cache=skip_cache,
        api_type=api_type,
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/classes/{object_class}/endpoints",
    response_model=JobStatusMultiDocResponse,
    summary="Get endpoints extraction status",
)
async def get_class_endpoints_status(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    jobId: Optional[UUID] = Query(None, description="Job ID (optional)"),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Get the status of endpoints extraction job for the specified object class.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    resolved_job_id = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key=f"{object_class}EndpointsJobId",
        job_label="endpoints",
        not_found_detail=f"No endpoints job found for {object_class} in session {session_id}",
    )

    response = await build_typed_job_status_response(resolved_job_id, EndpointResponse)
    return await results.refresh_endpoints_status(db, repo, response, session_id, object_class)


@router.put(
    "/{session_id}/classes/{object_class}/endpoints",
    summary="Override endpoints for object class",
)
async def override_class_endpoints(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    endpoints: Dict[str, Any] = Body(..., description="Endpoints data as JSON"),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Manually override the endpoints for an object class.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    object_class = normalize_object_class_name(object_class)
    await results.store_endpoints_override(db, repo, session_id, object_class, endpoints)

    return {
        "message": f"Endpoints for {object_class} overridden successfully",
        "sessionId": session_id,
        "objectClass": object_class,
    }
