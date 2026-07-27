# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Digester object-class endpoints."""

from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.responses import build_typed_job_status_response
from src.core.db import get_db
from src.database.repositories.session_repository import SessionRepository
from src.jobs.schema import JobCreateResponse, JobStatusMultiDocResponse
from src.modules.digester import orchestration, results
from src.modules.digester.schemas import ObjectClassesResponse
from src.session.access import ensure_session_exists, resolve_session_job_id
from src.shared.enums import ApiType

router = APIRouter(tags=["Digester: Object Classes"])


@router.post(
    "/{session_id}/classes",
    response_model=JobCreateResponse,
    summary="Extract object classes from documentation",
)
async def extract_object_classes(
    session_id: UUID = Path(..., description="Session ID"),
    skip_cache: bool = Query(False, alias="skipCache", description="Whether to skip cached data"),
    api_type: Optional[ApiType] = Query(
        None,
        alias="apiType",
        description="Override the API protocol (REST/SCIM/SQL); falls back to the detected apiType when omitted.",
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Extract object classes from documentation stored in or uploaded to the session.
    Returns all extracted object classes enriched with confidence (high/medium/low)
    ordered from highest to lowest confidence.
    Returns jobId to poll for results.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    job_id = await orchestration.schedule_object_class_extraction(
        repo=repo,
        session_id=session_id,
        skip_cache=skip_cache,
        api_type=api_type,
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/classes",
    response_model=JobStatusMultiDocResponse,
    summary="Get object classes extraction status",
)
async def get_object_classes_status(
    session_id: UUID = Path(..., description="Session ID"),
    jobId: Optional[UUID] = Query(None, description="Job ID (optional, will use session's job if not provided)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the status of object classes extraction job.
    If jobId is not provided, retrieves the job from session.
    Returns the current session data (which may include endpoints added after job completion).
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    resolved_job_id = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key="objectClassesJobId",
        job_label="object classes",
    )

    response = await build_typed_job_status_response(resolved_job_id, ObjectClassesResponse)
    return await results.refresh_object_classes_status(db, repo, response, session_id)


@router.get(
    "/{session_id}/classes/{object_class}",
    response_model=Dict[str, Any],
    summary="Get a specific object class",
)
async def get_specific_object_class(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get a specific object class by name from the session.
    Returns the object class with all its data including endpoints and attributes.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    return await results.build_object_class_detail(db, repo, session_id, object_class)


@router.put(
    "/{session_id}/classes",
    summary="Upload all object classes to session",
)
async def upload_all_object_classes(
    session_id: UUID = Path(..., description="Session ID"),
    object_classes_data: Dict[str, Any] = Body(..., description="Object classes data as JSON"),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload all object classes to the session.
    Expects a JSON body with objectClasses array.
    Replaces existing object classes in the session.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    await results.store_object_classes(db, repo, session_id, object_classes_data)

    return {
        "message": "All object classes uploaded successfully",
        "sessionId": session_id,
    }


@router.put(
    "/{session_id}/classes/{object_class}",
    summary="Upload one object class to session",
)
async def upload_one_object_class(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    object_class_data: Dict[str, Any] = Body(..., description="Object class data as JSON"),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload or update a specific object class in the session.
    If the object class already exists, it will be updated.
    If it doesn't exist, it will be added to the objectClasses array.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    updated = await results.upsert_object_class_in_session(db, repo, session_id, object_class, object_class_data)

    return {
        "message": f"Object class '{object_class}' {'updated' if updated else 'added'} successfully",
        "sessionId": session_id,
    }
