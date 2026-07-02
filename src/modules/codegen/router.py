# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Codegen endpoints for V2 API (session-centric).
All codegen operations are nested under sessions.
"""

from typing import Any, Mapping, Optional, cast
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database.config import get_db
from src.common.database.repositories.session_repository import SessionRepository
from src.common.enums import ApiType
from src.common.errors import (
    AttributesNotFoundError,
    RelationNotFoundError,
    RelationsNotFoundError,
)
from src.common.jobs import schedule_coroutine_job
from src.common.schema import (
    JobCreateResponse,
    JobStatusMultiDocResponse,
    JobStatusStageResponse,
)
from src.common.session.session import ensure_session_exists, resolve_session_job_id
from src.common.utils.normalize import normalize_object_class_name
from src.common.utils.relevance import hydrate_auth_sequences_from_relevance as _hydrate_auth_sequences_from_relevance
from src.common.utils.session_info_metadata import resolve_effective_api_type
from src.common.utils.status_response import build_multi_doc_status_response, build_stage_status_response
from src.modules.codegen import service
from src.modules.codegen.enums import SearchIntent, build_search_operation_key
from src.modules.codegen.orchestration import (
    context_payload_from_input,
    repair_context_from_input,
    schedule_operation_job,
)
from src.modules.codegen.schema import (
    AuthorizationCodegenInput,
    CodegenOperationInput,
    CodegenRepairContext,
    GroovyCodePayload,
)
from src.modules.codegen.selection.authorization import enrich_preferred_authorizations
from src.modules.digester.schemas import RelationsResponse

router = APIRouter()


def _preferred_authorizations_from_input(
    codegen_input: Optional[AuthorizationCodegenInput],
) -> Optional[list[dict]]:
    if codegen_input is None or not codegen_input.preferred_authorizations:
        return None
    return [authorization.model_dump(exclude_none=True) for authorization in codegen_input.preferred_authorizations]


# Codegen Operations - Authorization
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
    db: AsyncSession = Depends(get_db),
    codegen_input: AuthorizationCodegenInput = Body(...),
):
    """
    Generate connector-level Groovy authentication/authorization code from digester auth output.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    protocol = await resolve_effective_api_type(session_id, api_type)

    input_preferred_authorizations = _preferred_authorizations_from_input(codegen_input)

    auth_output_raw = await repo.get_session_data(session_id, "authOutput")
    if not isinstance(auth_output_raw, Mapping) or not auth_output_raw:
        auth_output: Mapping[str, Any] = {"auth": []}
    else:
        auth_output = cast(Mapping[str, Any], auth_output_raw)
        try:
            auth_output = cast(
                Mapping[str, Any],
                await _hydrate_auth_sequences_from_relevance(db, session_id, auth_output),
            )
        except Exception:
            pass

    preferred_authorizations = enrich_preferred_authorizations(
        auth_output,
        input_preferred_authorizations,
    )
    repair_context = codegen_input.repair_context() if codegen_input else None
    context_payload = codegen_input.context_payload() if codegen_input else {}

    job_input: dict[str, Any] = {
        "sessionId": session_id,
        "auth": auth_output,
        "skipCache": skip_cache,
        "apiType": protocol.value,
    }
    job_input.update(context_payload)
    if preferred_authorizations is not None:
        job_input["preferredAuthorizations"] = preferred_authorizations

    worker_kwargs: dict[str, Any] = {
        "auth_payload": auth_output,
        "preferred_authorizations": preferred_authorizations,
        "session_id": session_id,
        "protocol": protocol,
    }
    if repair_context is not None:
        worker_kwargs["repair_context"] = repair_context

    job_id = await schedule_coroutine_job(
        job_type="codegen.getAuthorization",
        input_payload=job_input,
        worker=service.generate_authorization_code,
        worker_args=(),
        worker_kwargs=worker_kwargs,
        initial_stage="preparing",
        initial_message="Preparing authorization code generation from relevant chunks",
        session_id=session_id,
        session_result_key="authorizationOutput",
    )

    session_input: dict[str, Any] = {}
    session_input.update(context_payload)
    if preferred_authorizations is not None:
        session_input["preferredAuthorizations"] = preferred_authorizations
    await repo.update_session(
        session_id,
        {
            "authorizationJobId": str(job_id),
            "authorizationInput": session_input,
        },
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
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
):
    """
    Manually override the authorization code.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    await repo.update_session(session_id, {"authorizationOutput": authorization_code.model_dump()})

    return {
        "message": "Authorization code overridden successfully",
        "sessionId": session_id,
    }


# Codegen Operations - Native Schema
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
    db: AsyncSession = Depends(get_db),
    codegen_input: Optional[CodegenRepairContext] = None,
):
    """
    Generate native Groovy schema from attributes.
    Loads attributes from session automatically.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    # Load attributes from session
    attrs = await repo.get_session_data(session_id, f"{object_class}AttributesOutput")
    if not attrs:
        raise AttributesNotFoundError(object_class, session_id)

    protocol = await resolve_effective_api_type(session_id, api_type)
    repair_context = repair_context_from_input(codegen_input)
    context_payload = context_payload_from_input(codegen_input)
    job_input = {
        "attributes": attrs,
        "objectClass": object_class,
        "skipCache": skip_cache,
        "apiType": protocol.value,
    }
    job_input.update(context_payload)
    worker_kwargs: dict[str, Any] = {"session_id": session_id, "protocol": protocol}
    if repair_context is not None:
        worker_kwargs["repair_context"] = repair_context

    job_id = await schedule_coroutine_job(
        job_type="codegen.getNativeSchema",
        input_payload=job_input,
        worker=service.generate_native_schema_code,
        worker_args=(attrs, object_class),
        worker_kwargs=worker_kwargs,
        initial_stage="queue",
        initial_message="Queued code generation",
        session_id=session_id,
        session_result_key=f"{object_class}NativeSchemaOutput",
    )

    await repo.update_session(
        session_id,
        {
            f"{object_class}NativeSchemaJobId": str(job_id),
            f"{object_class}NativeSchemaInput": {
                "attributes": attrs,
                "objectClass": object_class,
                **context_payload,
            },
        },
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
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
):
    """
    Manually override the native schema for an object class.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    await repo.update_session(session_id, {f"{object_class}NativeSchemaOutput": native_schema.model_dump()})

    return {
        "message": f"Native schema for {object_class} overridden successfully",
        "sessionId": session_id,
        "objectClass": object_class,
    }


# Codegen Operations - ConnID
@router.post(
    "/{session_id}/classes/{object_class}/connid",
    response_model=JobCreateResponse,
    summary="Generate ConnID for object class",
)
async def generate_connid(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    skip_cache: bool = Query(False, alias="skipCache", description="Whether to skip cached data for generation"),
    db: AsyncSession = Depends(get_db),
    codegen_input: Optional[CodegenRepairContext] = None,
):
    """
    Generate ConnID Groovy code from attributes.
    Loads attributes from session automatically.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    # Load attributes from session
    attrs = await repo.get_session_data(session_id, f"{object_class}AttributesOutput")
    if not attrs:
        raise AttributesNotFoundError(object_class, session_id)

    repair_context = repair_context_from_input(codegen_input)
    context_payload = context_payload_from_input(codegen_input)
    job_input = {
        "attributes": attrs,
        "objectClass": object_class,
        "skipCache": skip_cache,
    }
    job_input.update(context_payload)
    worker_kwargs: dict[str, Any] = {}
    if repair_context is not None:
        worker_kwargs["repair_context"] = repair_context

    job_id = await schedule_coroutine_job(
        job_type="codegen.getConnID",
        input_payload=job_input,
        worker=service.generate_conn_id_code,
        worker_args=(attrs, object_class),
        worker_kwargs=worker_kwargs,
        initial_stage="queue",
        initial_message="Queued code generation",
        session_id=session_id,
        session_result_key=f"{object_class}ConnidOutput",
    )

    await repo.update_session(
        session_id,
        {
            f"{object_class}ConnidJobId": str(job_id),
            f"{object_class}ConnidInput": {
                "attributes": attrs,
                "objectClass": object_class,
                **context_payload,
            },
        },
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
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
):
    """
    Manually override the ConnID for an object class.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    await repo.update_session(session_id, {f"{object_class}ConnidOutput": connid.model_dump()})

    return {
        "message": f"ConnID for {object_class} overridden successfully",
        "sessionId": session_id,
        "objectClass": object_class,
    }


# Codegen Operations - Search
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
    db: AsyncSession = Depends(get_db),
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
        worker=service.generate_search_code,
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
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
):
    """
    Manually override the search code for an object class.
    """
    object_class = normalize_object_class_name(object_class)
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    operation_key = build_search_operation_key(object_class, intent)
    await repo.update_session(session_id, {f"{operation_key}Output": search_code.model_dump()})

    return {
        "message": f"Search code for {object_class} overridden successfully",
        "sessionId": session_id,
        "objectClass": object_class,
    }


# Codegen Operations - Create
@router.post(
    "/{session_id}/classes/{object_class}/create",
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
        worker=service.generate_create_code,
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/classes/{object_class}/create",
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

    await repo.update_session(session_id, {f"{object_class}CreateOutput": create_code.model_dump()})

    return {
        "message": f"Create code for {object_class} overridden successfully",
        "sessionId": session_id,
        "objectClass": object_class,
    }


# Codegen Operations - Update
@router.post(
    "/{session_id}/classes/{object_class}/update",
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
        worker=service.generate_update_code,
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/classes/{object_class}/update",
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

    await repo.update_session(session_id, {f"{object_class}UpdateOutput": update_code.model_dump()})

    return {
        "message": f"Update code for {object_class} overridden successfully",
        "sessionId": session_id,
        "objectClass": object_class,
    }


# Codegen Operations - Delete
@router.post(
    "/{session_id}/classes/{object_class}/delete",
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
        worker=service.generate_delete_code,
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/classes/{object_class}/delete",
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

    await repo.update_session(session_id, {f"{object_class}DeleteOutput": delete_code.model_dump()})

    return {
        "message": f"Delete code for {object_class} overridden successfully",
        "sessionId": session_id,
        "objectClass": object_class,
    }


# Codegen Operations - Relations
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

    # Load relations from session
    relations_json = await repo.get_session_data(session_id, "relationsOutput")
    if not relations_json:
        raise RelationsNotFoundError(session_id)

    try:
        relations_model = RelationsResponse.model_validate(relations_json)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": "Stored relationsOutput is invalid. Re-run relations extraction or override the relations payload.",
                "errors": exc.errors(include_input=False),
            },
        ) from exc

    selected_relation = next(
        (relation for relation in relations_model.relations if relation.name == relation_name), None
    )
    if selected_relation is None:
        raise RelationNotFoundError(relation_name, session_id)

    selected_relations_model = RelationsResponse(relations=[selected_relation])
    relations_payload = selected_relations_model.model_dump(by_alias=True, mode="json")

    job_id = await schedule_coroutine_job(
        job_type="codegen.getRelation",
        input_payload={
            "relations": relations_payload,
            "relationName": relation_name,
            "sessionId": session_id,
            "skipCache": skip_cache,
        },
        worker=service.generate_relation_code,
        worker_kwargs={
            "relations": selected_relations_model,
            "relation_name": relation_name,
            "session_id": session_id,
        },
        initial_stage="preparing",
        initial_message="Queued code generation from relevant chunks",
        session_id=session_id,
        session_result_key=f"{relation_name}CodeOutput",
    )

    await repo.update_session(
        session_id,
        {
            f"{relation_name}CodeJobId": str(job_id),
            f"{relation_name}CodeInput": {"relations": relations_payload},
        },
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

    await repo.update_session(session_id, {f"{relation_name}CodeOutput": relation_code.model_dump()})

    return {
        "message": f"Relation code for {relation_name} overridden successfully",
        "sessionId": session_id,
        "relationName": relation_name,
    }
