# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Object-class connector fix endpoint."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.responses import build_typed_job_status_response
from src.core.db import DbSession
from src.database.repositories.session_repository import SessionRepository
from src.documents.normalize import normalize_object_class_name
from src.jobs.schema import JobCreateResponse, JobStatusMultiDocResponse
from src.modules.codegen.orchestration import schedule_connector_fix_job
from src.modules.codegen.schema import ConnectorFixInput, ConnectorFixResult
from src.session.access import ensure_session_exists, resolve_session_job_id
from src.shared.enums import ApiType

router = APIRouter(tags=["Codegen: Fix"])


@router.post(
    "/{session_id}/classes/{objectClass}/fix",
    response_model=JobCreateResponse,
    summary="Fix one object class from midPoint errors",
)
async def fix_connector(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(
        ...,
        alias="objectClass",
        description="Object class whose generated connector scripts should be fixed.",
    ),
    api_type: Optional[ApiType] = Query(
        None,
        alias="apiType",
        description="Override the API protocol (REST/SCIM/SQL); falls back to the detected apiType when omitted.",
    ),
    db: AsyncSession = DbSession,
    codegen_input: ConnectorFixInput = Body(..., description="midPoint errors and optional script overrides"),
):
    """
    Fix faulty Groovy scripts for one object class from the errors midPoint reported.

    Loads generated search, create, update, delete and native-schema code only for the selected
    object class; the native-schema script carries that class's ConnID attribute mapping.
    The model receives those scripts and the reported errors, and only
    changed scripts are written back. Optional scripts in the body must also belong
    to this object class. The session is updated only if the fix succeeds.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    job_id = await schedule_connector_fix_job(
        repo=repo,
        session_id=session_id,
        object_class=object_class,
        api_type=api_type,
        codegen_input=codegen_input,
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/classes/{objectClass}/fix",
    response_model=JobStatusMultiDocResponse,
    summary="Get object-class connector fix status",
)
async def get_connector_fix_status(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(
        ...,
        alias="objectClass",
        description="Object class whose connector fix status should be returned.",
    ),
    jobId: Optional[UUID] = Query(None, description="Job ID (optional)"),
    db: AsyncSession = DbSession,
):
    """
    Get the fix job status and complete selected script set for one object class.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    jobId = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key=f"{object_class}ConnectorFixJobId",
        job_label=f"{object_class} connector fix",
        not_found_detail=f"No connector fix job found for object class {object_class} in session {session_id}",
    )

    return await build_typed_job_status_response(jobId, ConnectorFixResult)
