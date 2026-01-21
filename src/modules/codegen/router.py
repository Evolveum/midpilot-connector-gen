#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

"""
Codegen endpoints for V2 API (session-centric).
All codegen operations are nested under sessions.
"""

from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...common.database.config import get_db
from ...common.database.repositories.session_repository import SessionRepository
from ...common.enums import JobStatus
from ...common.jobs import get_job_status, schedule_coroutine_job
from ...common.schema import (
    JobCreateResponse,
    JobStatusMultiDocResponse,
    JobStatusStageResponse,
)
from ...common.session.session import get_session_documentation
from ...common.status_response import build_stage_status_response
from ..digester.schema import RelationsResponse
from . import service

router = APIRouter()


async def _build_multi_doc_status_response(job_id: UUID) -> JobStatusMultiDocResponse:
    """
    Build a multi-document aware status response for codegen jobs.
    It forwards the progress dict as-is so multi-doc fields (processedDocuments,
    totalDocuments, currentDocument{docId, processedChunks, totalChunks}) are preserved.
    """
    status = await get_job_status(job_id)
    raw_status = status.get("status", JobStatus.not_found.value)
    enum_status = JobStatus(raw_status)

    return JobStatusMultiDocResponse(
        jobId=status.get("jobId", job_id),
        status=enum_status,
        createdAt=status.get("createdAt"),
        startedAt=status.get("startedAt"),
        updatedAt=status.get("updatedAt"),
        progress=status.get("progress"),
        result=status.get("result"),
        errors=status.get("errors"),
    )


# Codegen Operations - Native Schema
@router.post(
    "/{session_id}/classes/{object_class}/native-schema",
    response_model=JobCreateResponse,
    summary="Generate native schema for object class",
)
async def generate_native_schema(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate native Groovy schema from attributes.
    Loads attributes from session automatically.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    # Load attributes from session
    attrs = await repo.get_session_data(session_id, f"{object_class}AttributesOutput")
    if not attrs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No attributes found for {object_class} in session {session_id}. Please run /classes/{object_class}/attributes endpoint first.",
        )

    job_id = await schedule_coroutine_job(
        job_type="codegen.getNativeSchema",
        input_payload={"attributes": attrs, "objectClass": object_class},
        worker=service.create_native_schema,
        worker_args=(attrs, object_class),
        initial_stage="queue",
        initial_message="Queued code generation",
        session_id=session_id,
        session_result_key=f"{object_class}NativeSchema",
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
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    if not jobId:
        jobId = await repo.get_session_data(session_id, f"{object_class}NativeSchemaJobId")
        if not jobId:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No native schema job found for {object_class} in session {session_id}",
            )

    return await build_stage_status_response(jobId)


@router.put(
    "/{session_id}/classes/{object_class}/native-schema",
    summary="Override native schema",
)
async def override_native_schema(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    native_schema: Dict[str, Any] = Body(..., description="Native schema code as JSON"),
    db: AsyncSession = Depends(get_db),
):
    """
    Manually override the native schema for an object class.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    await repo.update_session(session_id, {f"{object_class}NativeSchema": native_schema})

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
    db: AsyncSession = Depends(get_db),
):
    """
    Generate ConnID Groovy code from attributes.
    Loads attributes from session automatically.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    # Load attributes from session
    attrs = await repo.get_session_data(session_id, f"{object_class}AttributesOutput")
    if not attrs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No attributes found for {object_class} in session {session_id}. Please run /classes/{object_class}/attributes endpoint first.",
        )

    job_id = await schedule_coroutine_job(
        job_type="codegen.getConnID",
        input_payload={"attributes": attrs, "objectClass": object_class},
        worker=service.create_conn_id,
        worker_args=(attrs, object_class),
        initial_stage="queue",
        initial_message="Queued code generation",
        session_id=session_id,
        session_result_key=f"{object_class}Connid",
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
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    if not jobId:
        jobId = await repo.get_session_data(session_id, f"{object_class}ConnidJobId")
        if not jobId:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No ConnID job found for {object_class} in session {session_id}",
            )

    return await build_stage_status_response(jobId)


@router.put(
    "/{session_id}/classes/{object_class}/connid",
    summary="Override ConnID",
)
async def override_connid(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    connid: Dict[str, Any] = Body(..., description="ConnID code as JSON"),
    db: AsyncSession = Depends(get_db),
):
    """
    Manually override the ConnID for an object class.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    await repo.update_session(session_id, {f"{object_class}Connid": connid})

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
    intent: str = Path(..., description="Intent"),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate Groovy search code for the given object class.
    Loads attributes and endpoints from session automatically.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    # Get documentation items and concatenate for fallback
    doc_items = await get_session_documentation(session_id, db=db)
    doc_text = "\n\n---\n\n".join([item["content"] for item in doc_items])

    # Load attributes from session
    attrs = await repo.get_session_data(session_id, f"{object_class}AttributesOutput")
    if not attrs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No attributes found for {object_class} in session {session_id}. Please run /classes/{object_class}/attributes endpoint first.",
        )

    # Load endpoints from session
    eps = await repo.get_session_data(session_id, f"{object_class}EndpointsOutput")
    if not eps:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No endpoints found for {object_class} in session {session_id}. Please run /classes/{object_class}/endpoints endpoint first.",
        )

    job_id = await schedule_coroutine_job(
        job_type="codegen.getSearch",
        input_payload={
            "sessionId": session_id,
            "attributes": attrs,
            "endpoints": eps,
            "object_class": object_class,
        },
        worker=service.create_search,
        worker_args=(),
        worker_kwargs={
            "attributes": attrs,
            "endpoints": eps,
            "session_id": session_id,
            "documentation": doc_text,
            "documentation_items": doc_items,
            "object_class": object_class,
        },
        initial_stage="chunking",
        initial_message="Preparing code generation from relevant chunks",
        session_id=session_id,
        session_result_key=f"{object_class}Search",
    )

    await repo.update_session(
        session_id,
        {
            f"{object_class}SearchJobId": str(job_id),
            f"{object_class}SearchInput": {"objectClass": object_class, "attributes": attrs, "endpoints": eps},
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
    intent: str = Path(..., description="Intent"),
    jobId: Optional[UUID] = Query(None, description="Job ID (optional)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the status of search code generation job.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    if not jobId:
        jobId = await repo.get_session_data(session_id, f"{object_class}SearchJobId")
        if not jobId:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No search job found for {object_class} in session {session_id}",
            )

    return await _build_multi_doc_status_response(jobId)


@router.put(
    "/{session_id}/classes/{object_class}/search/{intent}",
    summary="Override search code",
)
async def override_search(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    intent: str = Path(..., description="Intent"),
    search_code: Dict[str, Any] = Body(..., description="Search code as JSON"),
    db: AsyncSession = Depends(get_db),
):
    """
    Manually override the search code for an object class.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    await repo.update_session(session_id, {f"{object_class}Search": search_code})

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
    db: AsyncSession = Depends(get_db),
):
    """
    Generate Groovy create code for the given object class.
    Loads attributes and endpoints from session automatically.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    # Get documentation items and concatenate for fallback
    doc_items = await get_session_documentation(session_id, db=db)
    doc_text = "\n\n---\n\n".join([item["content"] for item in doc_items])

    # Load attributes from session
    attrs = await repo.get_session_data(session_id, f"{object_class}AttributesOutput")
    if not attrs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No attributes found for {object_class} in session {session_id}. Please run /classes/{object_class}/attributes endpoint first.",
        )

    # Load endpoints from session
    eps = await repo.get_session_data(session_id, f"{object_class}EndpointsOutput")
    if not eps:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No endpoints found for {object_class} in session {session_id}. Please run /classes/{object_class}/endpoints endpoint first.",
        )

    job_id = await schedule_coroutine_job(
        job_type="codegen.getCreate",
        input_payload={
            "sessionId": session_id,
            "attributes": attrs,
            "endpoints": eps,
            "object_class": object_class,
        },
        worker=service.create_create,
        worker_args=(),
        worker_kwargs={
            "attributes": attrs,
            "endpoints": eps,
            "session_id": session_id,
            "documentation": doc_text,
            "documentation_items": doc_items,
            "object_class": object_class,
        },
        initial_stage="chunking",
        initial_message="Preparing code generation from relevant chunks",
        session_id=session_id,
        session_result_key=f"{object_class}Create",
    )

    await repo.update_session(
        session_id,
        {
            f"{object_class}CreateJobId": str(job_id),
            f"{object_class}CreateInput": {"objectClass": object_class, "attributes": attrs, "endpoints": eps},
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
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    if not jobId:
        job_id_raw = await repo.get_session_data(session_id, f"{object_class}CreateJobId")
        if not job_id_raw:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No create job found for {object_class} in session {session_id}",
            )
        jobId = UUID(str(job_id_raw)) if not isinstance(job_id_raw, UUID) else job_id_raw

    return await _build_multi_doc_status_response(jobId)


@router.put(
    "/{session_id}/classes/{object_class}/create",
    summary="Override create code",
)
async def override_create(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    create_code: Dict[str, Any] = Body(..., description="Create code as JSON"),
    db: AsyncSession = Depends(get_db),
):
    """
    Manually override the create code for an object class.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    await repo.update_session(session_id, {f"{object_class}Create": create_code})

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
    db: AsyncSession = Depends(get_db),
):
    """
    Generate Groovy update code for the given object class.
    Loads attributes and endpoints from session automatically.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    # Get documentation items and concatenate for fallback
    doc_items = await get_session_documentation(session_id, db=db)
    doc_text = "\n\n---\n\n".join([item["content"] for item in doc_items])

    # Load attributes from session
    attrs = await repo.get_session_data(session_id, f"{object_class}AttributesOutput")
    if not attrs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No attributes found for {object_class} in session {session_id}. Please run /classes/{object_class}/attributes endpoint first.",
        )

    # Load endpoints from session
    eps = await repo.get_session_data(session_id, f"{object_class}EndpointsOutput")
    if not eps:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No endpoints found for {object_class} in session {session_id}. Please run /classes/{object_class}/endpoints endpoint first.",
        )

    job_id = await schedule_coroutine_job(
        job_type="codegen.getUpdate",
        input_payload={
            "sessionId": session_id,
            "attributes": attrs,
            "endpoints": eps,
            "object_class": object_class,
        },
        worker=service.create_update,
        worker_args=(),
        worker_kwargs={
            "attributes": attrs,
            "endpoints": eps,
            "session_id": session_id,
            "documentation": doc_text,
            "documentation_items": doc_items,
            "object_class": object_class,
        },
        initial_stage="chunking",
        initial_message="Preparing code generation from relevant chunks",
        session_id=session_id,
        session_result_key=f"{object_class}Update",
    )

    await repo.update_session(
        session_id,
        {
            f"{object_class}UpdateJobId": str(job_id),
            f"{object_class}UpdateInput": {"objectClass": object_class, "attributes": attrs, "endpoints": eps},
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
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    if not jobId:
        job_id_raw = await repo.get_session_data(session_id, f"{object_class}UpdateJobId")
        if not job_id_raw:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No update job found for {object_class} in session {session_id}",
            )
        jobId = UUID(str(job_id_raw)) if not isinstance(job_id_raw, UUID) else job_id_raw

    return await _build_multi_doc_status_response(jobId)


@router.put(
    "/{session_id}/classes/{object_class}/update",
    summary="Override update code",
)
async def override_update(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    update_code: Dict[str, Any] = Body(..., description="Update code as JSON"),
    db: AsyncSession = Depends(get_db),
):
    """
    Manually override the update code for an object class.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    await repo.update_session(session_id, {f"{object_class}Update": update_code})

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
    db: AsyncSession = Depends(get_db),
):
    """
    Generate Groovy delete code for the given object class.
    Loads attributes and endpoints from session automatically.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    # Get documentation items and concatenate for fallback
    doc_items = await get_session_documentation(session_id, db=db)
    doc_text = "\n\n---\n\n".join([item["content"] for item in doc_items])

    # Load attributes from session
    attrs = await repo.get_session_data(session_id, f"{object_class}AttributesOutput")
    if not attrs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No attributes found for {object_class} in session {session_id}. Please run /classes/{object_class}/attributes endpoint first.",
        )

    # Load endpoints from session
    eps = await repo.get_session_data(session_id, f"{object_class}EndpointsOutput")
    if not eps:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No endpoints found for {object_class} in session {session_id}. Please run /classes/{object_class}/endpoints endpoint first.",
        )

    job_id = await schedule_coroutine_job(
        job_type="codegen.getDelete",
        input_payload={
            "sessionId": session_id,
            "attributes": attrs,
            "endpoints": eps,
            "object_class": object_class,
        },
        worker=service.create_delete,
        worker_args=(),
        worker_kwargs={
            "attributes": attrs,
            "endpoints": eps,
            "session_id": session_id,
            "documentation": doc_text,
            "documentation_items": doc_items,
            "object_class": object_class,
        },
        initial_stage="chunking",
        initial_message="Preparing code generation from relevant chunks",
        session_id=session_id,
        session_result_key=f"{object_class}Delete",
    )

    await repo.update_session(
        session_id,
        {
            f"{object_class}DeleteJobId": str(job_id),
            f"{object_class}DeleteInput": {"objectClass": object_class, "attributes": attrs, "endpoints": eps},
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
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    if not jobId:
        job_id_raw = await repo.get_session_data(session_id, f"{object_class}DeleteJobId")
        if not job_id_raw:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No delete job found for {object_class} in session {session_id}",
            )
        jobId = UUID(str(job_id_raw)) if not isinstance(job_id_raw, UUID) else job_id_raw

    return await _build_multi_doc_status_response(jobId)


@router.put(
    "/{session_id}/classes/{object_class}/delete",
    summary="Override delete code",
)
async def override_delete(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    delete_code: Dict[str, Any] = Body(..., description="Delete code as JSON"),
    db: AsyncSession = Depends(get_db),
):
    """
    Manually override the delete code for an object class.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    await repo.update_session(session_id, {f"{object_class}Delete": delete_code})

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
    db: AsyncSession = Depends(get_db),
):
    """
    Generate Groovy relation code.
    Loads relations from session automatically.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    # Get documentation items and concatenate for fallback
    doc_items = await get_session_documentation(session_id, db=db)
    doc_text = "\n\n---\n\n".join([item["content"] for item in doc_items])

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
        input_payload={"relations": relations_json, "sessionId": session_id},
        worker=service.create_relation,
        worker_kwargs={
            "relations": relations_model,
            "session_id": session_id,
            "documentation": doc_text,
            "documentation_items": doc_items,
        },
        initial_stage="queue",
        initial_message="Queued code generation from relevant chunks",
        session_id=session_id,
        session_result_key=f"{relation_name}Code",
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
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    if not jobId:
        jobId = await repo.get_session_data(session_id, f"{relation_name}CodeJobId")
        if not jobId:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No relation code job found for {relation_name} in session {session_id}",
            )

    return await _build_multi_doc_status_response(jobId)


@router.put(
    "/{session_id}/relations/{relation_name}",
    summary="Override relation code",
)
async def override_relation_code(
    session_id: UUID = Path(..., description="Session ID"),
    relation_name: str = Path(..., description="Relation name"),
    relation_code: Dict[str, Any] = Body(..., description="Relation code as JSON"),
    db: AsyncSession = Depends(get_db),
):
    """
    Manually override the relation code.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    await repo.update_session(session_id, {f"{relation_name}Code": relation_code})

    return {
        "message": f"Relation code for {relation_name} overridden successfully",
        "sessionId": session_id,
        "relationName": relation_name,
    }
