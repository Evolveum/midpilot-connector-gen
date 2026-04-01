# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Codegen endpoints for V2 API (session-centric).
All codegen operations are nested under sessions.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database.config import get_db
from src.common.database.repositories.session_repository import SessionRepository
from src.common.jobs import schedule_coroutine_job
from src.common.schema import (
    JobCreateResponse,
    JobStatusMultiDocResponse,
    JobStatusStageResponse,
)
from src.common.session.session import ensure_session_exists, resolve_session_job_id
from src.common.utils.session_info_metadata import get_session_api_types, is_scim_api
from src.common.utils.status_response import build_multi_doc_status_response, build_stage_status_response
from src.modules.codegen import service
from src.modules.codegen.schema import GroovyCodePayload, SearchIntent, build_search_operation_key
from src.modules.digester.schema import RelationsResponse

router = APIRouter()


# Codegen Operations - Native Schema
@router.post(
    "/{session_id}/classes/{object_class}/native-schema",
    response_model=JobCreateResponse,
    summary="Generate native schema for object class",
)
async def generate_native_schema(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    usePreviousSessionData: bool = Query(True, description="Whether to use previous session data for generation"),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate native Groovy schema from attributes.
    Loads attributes from session automatically.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    # Load attributes from session
    attrs = await repo.get_session_data(session_id, f"{object_class}AttributesOutput")
    if not attrs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No attributes found for {object_class} in session {session_id}. Please run /classes/{object_class}/attributes endpoint first.",
        )

    job_id = await schedule_coroutine_job(
        job_type="codegen.getNativeSchema",
        input_payload={
            "attributes": attrs,
            "objectClass": object_class,
            "usePreviousSessionData": usePreviousSessionData,
        },
        worker=service.create_native_schema,
        worker_args=(attrs, object_class),
        worker_kwargs={"session_id": session_id},
        initial_stage="queue",
        initial_message="Queued code generation",
        session_id=session_id,
        session_result_key=f"{object_class}NativeSchemaOutput",
    )

    await repo.update_session(
        session_id,
        {
            f"{object_class}NativeSchemaJobId": str(job_id),
            f"{object_class}NativeSchemaInput": {"attributes": attrs, "objectClass": object_class},
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
    usePreviousSessionData: bool = Query(True, description="Whether to use previous session data for generation"),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate ConnID Groovy code from attributes.
    Loads attributes from session automatically.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    # Load attributes from session
    attrs = await repo.get_session_data(session_id, f"{object_class}AttributesOutput")
    if not attrs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No attributes found for {object_class} in session {session_id}. Please run /classes/{object_class}/attributes endpoint first.",
        )

    job_id = await schedule_coroutine_job(
        job_type="codegen.getConnID",
        input_payload={
            "attributes": attrs,
            "objectClass": object_class,
            "usePreviousSessionData": usePreviousSessionData,
        },
        worker=service.create_conn_id,
        worker_args=(attrs, object_class),
        initial_stage="queue",
        initial_message="Queued code generation",
        session_id=session_id,
        session_result_key=f"{object_class}ConnidOutput",
    )

    await repo.update_session(
        session_id,
        {
            f"{object_class}ConnidJobId": str(job_id),
            f"{object_class}ConnidInput": {"attributes": attrs, "objectClass": object_class},
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
    usePreviousSessionData: bool = Query(True, description="Whether to use previous session data for generation"),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate Groovy search code for the given object class.
    Loads attributes and endpoints from session automatically.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    # Load attributes from session
    attrs = await repo.get_session_data(session_id, f"{object_class}AttributesOutput")
    if not attrs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No attributes found for {object_class} in session {session_id}. Please run /classes/{object_class}/attributes endpoint first.",
        )

    api_types = await get_session_api_types(session_id)
    is_scim = is_scim_api(api_types)

    eps = await repo.get_session_data(session_id, f"{object_class}EndpointsOutput")
    if eps is None and not is_scim:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No endpoints found for {object_class} in session {session_id}. Please run /classes/{object_class}/endpoints endpoint first.",
        )

    job_input = {
        "sessionId": session_id,
        "attributes": attrs,
        "object_class": object_class,
        "intent": intent,
        "usePreviousSessionData": usePreviousSessionData,
    }
    worker_kwargs = {
        "attributes": attrs,
        "session_id": session_id,
        "object_class": object_class,
        "intent": intent,
    }
    if eps is not None:
        job_input["endpoints"] = eps
        worker_kwargs["endpoints"] = eps

    operation_key = build_search_operation_key(object_class, intent)

    job_id = await schedule_coroutine_job(
        job_type="codegen.getSearch",
        input_payload=job_input,
        worker=service.create_search,
        worker_args=(),
        worker_kwargs=worker_kwargs,
        initial_stage="preparing",
        initial_message="Preparing code generation from relevant chunks",
        session_id=session_id,
        session_result_key=f"{operation_key}Output",
    )

    session_input = {"objectClass": object_class, "attributes": attrs, "intent": intent}
    if eps is not None:
        session_input["endpoints"] = eps
    await repo.update_session(
        session_id,
        {
            f"{operation_key}JobId": str(job_id),
            f"{operation_key}Input": session_input,
        },
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
    usePreviousSessionData: bool = Query(True, description="Whether to use previous session data for generation"),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate Groovy create code for the given object class.
    Loads attributes and endpoints from session automatically.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    # Load attributes from session
    attrs = await repo.get_session_data(session_id, f"{object_class}AttributesOutput")
    if not attrs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No attributes found for {object_class} in session {session_id}. Please run /classes/{object_class}/attributes endpoint first.",
        )

    api_types = await get_session_api_types(session_id)
    is_scim = is_scim_api(api_types)

    eps = await repo.get_session_data(session_id, f"{object_class}EndpointsOutput")
    if eps is None and not is_scim:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No endpoints found for {object_class} in session {session_id}. Please run /classes/{object_class}/endpoints endpoint first.",
        )

    job_input = {
        "sessionId": session_id,
        "attributes": attrs,
        "object_class": object_class,
        "usePreviousSessionData": usePreviousSessionData,
    }
    worker_kwargs = {
        "attributes": attrs,
        "session_id": session_id,
        "object_class": object_class,
    }
    if eps is not None:
        job_input["endpoints"] = eps
        worker_kwargs["endpoints"] = eps

    job_id = await schedule_coroutine_job(
        job_type="codegen.getCreate",
        input_payload=job_input,
        worker=service.create_create,
        worker_args=(),
        worker_kwargs=worker_kwargs,
        initial_stage="preparing",
        initial_message="Preparing code generation from relevant chunks",
        session_id=session_id,
        session_result_key=f"{object_class}CreateOutput",
    )

    session_input = {"objectClass": object_class, "attributes": attrs}
    if eps is not None:
        session_input["endpoints"] = eps
    await repo.update_session(
        session_id,
        {
            f"{object_class}CreateJobId": str(job_id),
            f"{object_class}CreateInput": session_input,
        },
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
    usePreviousSessionData: bool = Query(True, description="Whether to use previous session data for generation"),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate Groovy update code for the given object class.
    Loads attributes and endpoints from session automatically.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    # Load attributes from session
    attrs = await repo.get_session_data(session_id, f"{object_class}AttributesOutput")
    if not attrs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No attributes found for {object_class} in session {session_id}. Please run /classes/{object_class}/attributes endpoint first.",
        )

    api_types = await get_session_api_types(session_id)
    is_scim = is_scim_api(api_types)

    eps = await repo.get_session_data(session_id, f"{object_class}EndpointsOutput")
    if eps is None and not is_scim:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No endpoints found for {object_class} in session {session_id}. Please run /classes/{object_class}/endpoints endpoint first.",
        )

    job_input = {
        "sessionId": session_id,
        "attributes": attrs,
        "object_class": object_class,
        "usePreviousSessionData": usePreviousSessionData,
    }
    worker_kwargs = {
        "attributes": attrs,
        "session_id": session_id,
        "object_class": object_class,
    }
    if eps is not None:
        job_input["endpoints"] = eps
        worker_kwargs["endpoints"] = eps

    job_id = await schedule_coroutine_job(
        job_type="codegen.getUpdate",
        input_payload=job_input,
        worker=service.create_update,
        worker_args=(),
        worker_kwargs=worker_kwargs,
        initial_stage="preparing",
        initial_message="Preparing code generation from relevant chunks",
        session_id=session_id,
        session_result_key=f"{object_class}UpdateOutput",
    )

    session_input = {"objectClass": object_class, "attributes": attrs}
    if eps is not None:
        session_input["endpoints"] = eps
    await repo.update_session(
        session_id,
        {
            f"{object_class}UpdateJobId": str(job_id),
            f"{object_class}UpdateInput": session_input,
        },
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
    usePreviousSessionData: bool = Query(True, description="Whether to use previous session data for generation"),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate Groovy delete code for the given object class.
    Loads attributes and endpoints from session automatically.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    # Load attributes from session
    attrs = await repo.get_session_data(session_id, f"{object_class}AttributesOutput")
    if not attrs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No attributes found for {object_class} in session {session_id}. Please run /classes/{object_class}/attributes endpoint first.",
        )

    api_types = await get_session_api_types(session_id)
    is_scim = is_scim_api(api_types)

    eps = await repo.get_session_data(session_id, f"{object_class}EndpointsOutput")
    if eps is None and not is_scim:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No endpoints found for {object_class} in session {session_id}. Please run /classes/{object_class}/endpoints endpoint first.",
        )

    job_input = {
        "sessionId": session_id,
        "attributes": attrs,
        "object_class": object_class,
        "usePreviousSessionData": usePreviousSessionData,
    }
    worker_kwargs = {
        "attributes": attrs,
        "session_id": session_id,
        "object_class": object_class,
    }
    if eps is not None:
        job_input["endpoints"] = eps
        worker_kwargs["endpoints"] = eps

    job_id = await schedule_coroutine_job(
        job_type="codegen.getDelete",
        input_payload=job_input,
        worker=service.create_delete,
        worker_args=(),
        worker_kwargs=worker_kwargs,
        initial_stage="preparing",
        initial_message="Preparing code generation from relevant chunks",
        session_id=session_id,
        session_result_key=f"{object_class}DeleteOutput",
    )

    session_input = {"objectClass": object_class, "attributes": attrs}
    if eps is not None:
        session_input["endpoints"] = eps
    await repo.update_session(
        session_id,
        {
            f"{object_class}DeleteJobId": str(job_id),
            f"{object_class}DeleteInput": session_input,
        },
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
    usePreviousSessionData: bool = Query(True, description="Whether to use previous session data for generation"),
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No relations found in session {session_id}. Please run /relations endpoint first.",
        )

    relations_model = RelationsResponse.model_validate(relations_json)

    job_id = await schedule_coroutine_job(
        job_type="codegen.getRelation",
        input_payload={
            "relations": relations_json,
            "relationName": relation_name,
            "sessionId": session_id,
            "usePreviousSessionData": usePreviousSessionData,
        },
        worker=service.create_relation,
        worker_kwargs={
            "relations": relations_model,
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
            f"{relation_name}CodeInput": {"relations": relations_json},
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
