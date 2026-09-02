# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Codegen authorization endpoints."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.responses import build_multi_doc_status_response
from src.core.db import get_db
from src.database.repositories.session_repository import SessionRepository
from src.jobs.schema import JobCreateResponse, JobStatusMultiDocResponse
from src.modules.codegen.orchestration import schedule_authorization_job
from src.modules.codegen.persistence import store_authorization_override
from src.modules.codegen.schema import AuthorizationCodegenInput, GroovyCodePayload
from src.session.access import ensure_session_exists, resolve_session_job_id
from src.shared.enums import ApiType

router = APIRouter(tags=["Codegen: Authorization"])


@router.post(
    "/{session_id}/authorization",
    response_model=JobCreateResponse,
    summary="Generate authorization code",
)
async def generate_authorization(
    session_id: UUID = Path(..., description="Session ID"),
    skip_cache: bool = Query(False, alias="skipCache", description="Whether to skip cached data for generation"),
    api_type: Optional[ApiType] = Query(
        None,
        alias="apiType",
        description="Override the API protocol (REST/SCIM/SQL); falls back to the detected apiType when omitted.",
    ),
    db: AsyncSession = Depends(get_db, scope="function"),
    codegen_input: AuthorizationCodegenInput = Body(...),
):
    """
    Generate connector-level Groovy authentication/authorization code from digester auth output.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    job_id = await schedule_authorization_job(
        db=db,
        repo=repo,
        session_id=session_id,
        api_type=api_type,
        skip_cache=skip_cache,
        codegen_input=codegen_input,
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/authorization",
    response_model=JobStatusMultiDocResponse,
    summary="Get authorization generation status",
)
async def get_authorization_status(
    session_id: UUID = Path(..., description="Session ID"),
    jobId: Optional[UUID] = Query(None, description="Job ID (optional)"),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Get the status of authorization code generation job.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    jobId = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key="authorizationJobId",
        job_label="authorization",
        not_found_detail=f"No authorization job found in session {session_id}",
    )

    return await build_multi_doc_status_response(jobId)


@router.put(
    "/{session_id}/authorization",
    summary="Override authorization code",
)
async def override_authorization(
    session_id: UUID = Path(..., description="Session ID"),
    authorization_code: GroovyCodePayload = Body(..., description="Authorization code as JSON"),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Manually override the authorization code.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)
    await store_authorization_override(repo, session_id, authorization_code)

    return {
        "message": "Authorization code overridden successfully",
        "sessionId": session_id,
    }
