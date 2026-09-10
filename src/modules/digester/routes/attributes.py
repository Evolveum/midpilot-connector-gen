# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Digester object-class attribute endpoints."""

from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Body, Path, Query
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.responses import build_typed_job_status_response
from src.core.db import DbSession
from src.database.repositories.session_repository import SessionRepository
from src.documents.normalize import normalize_object_class_name
from src.jobs.schema import JobCreateResponse, JobStatusMultiDocResponse
from src.modules.digester import orchestration, results
from src.modules.digester.schemas import AttributeResponse
from src.session.access import ensure_session_exists, resolve_session_job_id
from src.shared.enums import ApiType

router = APIRouter(tags=["Digester: Attributes"])


class AttributeJobStatusResponse(JobStatusMultiDocResponse):
    """Attribute status response with its finished result exposed in OpenAPI."""

    result: Optional[AttributeResponse] = Field(
        default=None,
        description="Extracted attributes and protocol-specific context when the job is finished.",
    )


@router.post(
    "/{session_id}/classes/{object_class}/attributes",
    response_model=JobCreateResponse,
    summary="Extract attributes for object class",
)
async def extract_class_attributes(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name (e.g., 'User', 'Group')"),
    skip_cache: bool = Query(False, alias="skipCache", description="Whether to skip cached data"),
    api_type: Optional[ApiType] = Query(
        None,
        alias="apiType",
        description="Override the API protocol (REST/SCIM/SQL); falls back to the detected apiType when omitted.",
    ),
    db: AsyncSession = DbSession,
):
    """
    Extract attributes schema for a specific object class.
    Only processes chunks that are relevant to the object class (from relevantDocumentations).
    Updates both {object_class}AttributesOutput and the attributes field in the specific object class.

    NOTE: We dont need to await documentation here, as it should have already been awaited during object class extraction.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    job_id = await orchestration.schedule_attribute_extraction(
        db=db,
        repo=repo,
        session_id=session_id,
        object_class=object_class,
        skip_cache=skip_cache,
        api_type=api_type,
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/classes/{object_class}/attributes",
    response_model=AttributeJobStatusResponse,
    summary="Get attributes extraction status",
)
async def get_class_attributes_status(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    jobId: Optional[UUID] = Query(None, description="Job ID (optional)"),
    db: AsyncSession = DbSession,
):
    """
    Get the status of attributes extraction job for the specified object class.
    Returns the current session data (which may have been updated after job completion).
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    resolved_job_id = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key=f"{object_class}AttributesJobId",
        job_label="attributes",
        not_found_detail=f"No attributes job found for {object_class} in session {session_id}",
    )

    # Get job status but override result with current session data
    response = await build_typed_job_status_response(resolved_job_id, AttributeResponse)
    return await results.refresh_attributes_status(db, repo, response, session_id, object_class)


@router.put(
    "/{session_id}/classes/{object_class}/attributes",
    summary="Override attributes for object class",
)
async def override_class_attributes(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    attributes: Dict[str, Any] = Body(..., description="Attributes schema as JSON"),
    db: AsyncSession = DbSession,
):
    """
    Manually override the attributes for an object class.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    object_class = normalize_object_class_name(object_class)
    await results.store_attributes_override(db, repo, session_id, object_class, attributes)

    return {
        "message": f"Attributes for {object_class} overridden successfully",
        "sessionId": session_id,
        "objectClass": object_class,
    }
