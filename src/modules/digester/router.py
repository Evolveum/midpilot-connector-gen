# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.chunk_filter.filter import filter_documentation_items
from src.common.database.config import get_db
from src.common.database.repositories.session_repository import SessionRepository
from src.common.enums import JobStatus
from src.common.jobs import schedule_coroutine_job
from src.common.schema import JobCreateResponse, JobStatusMultiDocResponse
from src.common.session.session import ensure_session_exists, get_session_documentation, resolve_session_job_id
from src.common.utils.session_metadata import get_session_api_types, get_session_base_api_url, is_scim_api
from src.modules.digester import service
from src.modules.digester.schema import (
    AttributeResponse,
    AuthResponse,
    EndpointResponse,
    InfoResponse,
    ObjectClassesResponse,
    RelationsResponse,
)
from src.modules.digester.utils.criteria import DEFAULT_CRITERIA, ENDPOINT_CRITERIA
from src.modules.digester.utils.inputs import auth_input, metadata_input, object_classes_input
from src.modules.digester.utils.object_classes import (
    find_object_class,
    get_relevant_chunks,
    normalize_object_class_name,
    upsert_object_class,
)
from src.modules.digester.utils.status import build_typed_job_status_response

router = APIRouter()


# Digester Operations - Object Classes
@router.post(
    "/{session_id}/classes",
    response_model=JobCreateResponse,
    summary="Extract object classes from documentation",
)
async def extract_object_classes(
    session_id: UUID = Path(..., description="Session ID"),
    filter_relevancy: bool = Query(True, description="Filter object classes by relevancy"),
    min_relevancy_level: str = Query("high", description="Minimum relevancy level (low/medium/high)"),
    use_previous_session_data: bool = Query(True, description="Whether to use previous session data if available"),
    db: AsyncSession = Depends(get_db),
):
    """
    Extract object classes from documentation stored in or uploaded to the session.
    Optionally filter documentation items based on provided criteria.
    Returns jobId to poll for results.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    job_id = await schedule_coroutine_job(
        job_type="digester.getObjectClass",
        input_payload={
            "filterRelevancy": filter_relevancy,
            "minRelevancyLevel": min_relevancy_level,
            "usePreviousSessionData": use_previous_session_data,
        },
        dynamic_input_enabled=True,
        dynamic_input_provider=object_classes_input,
        worker=service.extract_object_classes,
        worker_kwargs={
            "filter_relevancy": filter_relevancy,
            "min_relevancy_level": min_relevancy_level,
            "session_id": session_id,
        },
        initial_stage="chunking",
        initial_message="Preparing and splitting documentation",
        session_id=session_id,
        session_result_key="objectClassesOutput",
        await_documentation=True,
        await_documentation_timeout=750,
    )

    await repo.update_session(
        session_id,
        {
            "objectClassesJobId": str(job_id),
            "objectClassesInput": {
                "filterRelevancy": filter_relevancy,
                "minRelevancyLevel": min_relevancy_level,
                "usePreviousSessionData": use_previous_session_data,
            },
        },
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

    jobId = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key="objectClassesJobId",
        job_label="object classes",
    )

    response = await build_typed_job_status_response(jobId, ObjectClassesResponse)

    if response.status == JobStatus.finished:
        object_classes_output = await repo.get_session_data(session_id, "objectClassesOutput")
        if object_classes_output:
            try:
                # Validate and parse the session data
                response.result = ObjectClassesResponse.model_validate(object_classes_output)
            except Exception:
                # If validation fails, keep the original job result
                pass

    return response


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

    # Get object classes from session
    object_classes_output = await repo.get_session_data(session_id, "objectClassesOutput")
    if not object_classes_output or not isinstance(object_classes_output, dict):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No object classes found in session {session_id}. Please run /classes endpoint first.",
        )

    object_classes = object_classes_output.get("objectClasses", [])
    if not isinstance(object_classes, list):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invalid object classes data in session {session_id}",
        )

    target_object_class = find_object_class(object_classes, object_class)
    if target_object_class:
        result = target_object_class.copy()
        normalized_name = normalize_object_class_name(object_class)

        # Get attributes from session
        attributes_output = await repo.get_session_data(session_id, f"{normalized_name}AttributesOutput")
        if attributes_output and isinstance(attributes_output, dict):
            result["attributes"] = attributes_output.get("attributes", {})

        # Get endpoints from session
        endpoints_output = await repo.get_session_data(session_id, f"{normalized_name}EndpointsOutput")
        if endpoints_output and isinstance(endpoints_output, dict):
            result["endpoints"] = endpoints_output.get("endpoints", [])

        return result

    # If not found, raise 404
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Object class '{object_class}' not found in session {session_id}",
    )


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

    await repo.update_session(session_id, {"objectClassesOutput": object_classes_data})

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

    object_classes_output = await repo.get_session_data(session_id, "objectClassesOutput")
    object_classes_output, updated = upsert_object_class(object_classes_output, object_class, object_class_data)

    await repo.update_session(session_id, {"objectClassesOutput": object_classes_output})

    return {
        "message": f"Object class '{object_class}' {'updated' if updated else 'added'} successfully",
        "sessionId": session_id,
    }


# Digester Operations - Object Class Attributes
@router.post(
    "/{session_id}/classes/{object_class}/attributes",
    response_model=JobCreateResponse,
    summary="Extract attributes for object class",
)
async def extract_class_attributes(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name (e.g., 'User', 'Group')"),
    use_previous_session_data: bool = Query(True, description="Whether to use previous session data if available"),
    db: AsyncSession = Depends(get_db),
):
    """
    Extract attributes schema for a specific object class.
    Only processes chunks that are relevant to the object class (from relevantDocumentations).
    Updates both {object_class}AttributesOutput and the attributes field in the specific object class.

    NOTE: We dont need to await documentation here, as it should have already been awaited during object class extraction.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    # Get the object class data to find relevant chunks
    object_classes_output = await repo.get_session_data(session_id, "objectClassesOutput")
    if not object_classes_output or not isinstance(object_classes_output, dict):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No object classes found in session {session_id}. Please run /classes endpoint first.",
        )

    object_classes = object_classes_output.get("objectClasses", [])
    target_object_class = find_object_class(object_classes, object_class)

    if not target_object_class:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Object class '{object_class}' not found in session {session_id}.",
        )

    relevant_chunks = get_relevant_chunks(target_object_class)
    if not relevant_chunks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No relevant chunks found for object class '{object_class}'. Cannot extract attributes.",
        )

    # Get full documentation to extract relevant chunks
    doc_items = await get_session_documentation(session_id, db=db)

    total_chunks = len(relevant_chunks)
    job_id = await schedule_coroutine_job(
        job_type="digester.getObjectClassSchema",
        input_payload={
            "documentationItems": doc_items,
            "objectClass": object_class,
            "relevantDocumentations": relevant_chunks,
            "usePreviousSessionData": use_previous_session_data,
        },
        worker=service.extract_attributes,
        worker_args=(doc_items, object_class, session_id, relevant_chunks),
        initial_stage="chunking",
        initial_message=f"Processing {total_chunks} relevant chunks for {object_class}",
        session_id=session_id,
        session_result_key=f"{object_class}AttributesOutput",
    )

    await repo.update_session(
        session_id,
        {
            f"{object_class}AttributesJobId": str(job_id),
            f"{object_class}AttributesInput": {
                "objectClass": object_class,
                "relevantDocumentationsCount": total_chunks,
            },
        },
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/classes/{object_class}/attributes",
    response_model=JobStatusMultiDocResponse,
    summary="Get attributes extraction status",
)
async def get_class_attributes_status(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    jobId: Optional[UUID] = Query(None, description="Job ID (optional)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the status of attributes extraction job for the specified object class.
    Returns the current session data (which may have been updated after job completion).
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    jobId = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key=f"{object_class}AttributesJobId",
        job_label="attributes",
        not_found_detail=f"No attributes job found for {object_class} in session {session_id}",
    )

    # Get job status but override result with current session data
    response = await build_typed_job_status_response(jobId, AttributeResponse)

    # If job is finished, replace result with current session data (which may have been updated)
    if response.status == JobStatus.finished:
        attributes_output = await repo.get_session_data(session_id, f"{object_class}AttributesOutput")
        if attributes_output:
            try:
                # Validate and parse the session data
                response.result = AttributeResponse.model_validate(attributes_output)
            except Exception:
                # If validation fails, keep the original job result
                pass

    return response


@router.put(
    "/{session_id}/classes/{object_class}/attributes",
    summary="Override attributes for object class",
)
async def override_class_attributes(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    attributes: Dict[str, Any] = Body(..., description="Attributes schema as JSON"),
    db: AsyncSession = Depends(get_db),
):
    """
    Manually override the attributes for an object class.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    object_class = normalize_object_class_name(object_class)
    await repo.update_session(session_id, {f"{object_class}AttributesOutput": attributes})

    return {
        "message": f"Attributes for {object_class} overridden successfully",
        "sessionId": session_id,
        "objectClass": object_class,
    }


# Digester Operations - Object Class Endpoints
@router.post(
    "/{session_id}/classes/{object_class}/endpoints",
    response_model=JobCreateResponse,
    summary="Extract endpoints for object class",
)
async def extract_class_endpoints(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    use_previous_session_data: bool = Query(True, description="Whether to use previous session data if available"),
    db: AsyncSession = Depends(get_db),
):
    """
    Extract API endpoints for a specific object class.
    Automatically loads base API URL from session metadata if available.
    Updates both {object_class}EndpointsOutput and the endpoints field in the specific object class.
    Only processes chunks that are relevant to the object class (from relevantDocumentations).

    NOTE: We dont need to await documentation here, as it should have already been awaited during object class extraction.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    # Get the object class data to find relevant chunks
    object_classes_output = await repo.get_session_data(session_id, "objectClassesOutput")
    if not object_classes_output or not isinstance(object_classes_output, dict):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No object classes found in session {session_id}. Please run /classes endpoint first.",
        )

    object_classes = object_classes_output.get("objectClasses", [])
    target_object_class = find_object_class(object_classes, object_class)

    if not target_object_class:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Object class '{object_class}' not found in session {session_id}.",
        )

    api_type = await get_session_api_types(session_id)
    base_api_url = await get_session_base_api_url(session_id)
    is_scim = is_scim_api(api_type)

    criteria = ENDPOINT_CRITERIA.model_copy()
    criteria.allowed_tags = [[normalize_object_class_name(object_class)], ["endpoint", "endpoints"]]
    relevant_chunks_full = await filter_documentation_items(criteria, session_id, db=db)

    # If we dont have relevant chunks with ENDPOINT_CRITERIA, try to find relevant chunks with DEFAULT_CRITERIA
    if not relevant_chunks_full:
        criteria = DEFAULT_CRITERIA.model_copy()
        relevant_chunks_full = await filter_documentation_items(criteria, session_id, db=db)

    relevant_chunks = [
        {"doc_id": str(chunk["docId"]), "chunk_id": str(chunk["chunkId"])}
        for chunk in relevant_chunks_full
        if chunk.get("docId") and chunk.get("chunkId")
    ]
    if not relevant_chunks and not is_scim:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No relevant chunks found for object class '{object_class}'. Cannot extract endpoints.",
        )
    if not relevant_chunks and is_scim:
        relevant_chunks = get_relevant_chunks(target_object_class)

    # Get full documentation to extract relevant chunks
    doc_items = await get_session_documentation(session_id, db=db)

    total_chunks = len(relevant_chunks)
    job_id = await schedule_coroutine_job(
        job_type="digester.getEndpoints",
        input_payload={
            "documentationItems": doc_items,
            "objectClass": object_class,
            "baseApiUrl": base_api_url,
            "relevantDocumentations": relevant_chunks,
            "usePreviousSessionData": use_previous_session_data,
        },
        worker=service.extract_endpoints,
        worker_args=(doc_items, object_class, session_id, relevant_chunks),
        worker_kwargs={"base_api_url": base_api_url},
        initial_stage="chunking",
        initial_message=f"Processing {total_chunks} relevant chunks for {object_class}",
        session_id=session_id,
        session_result_key=f"{object_class}EndpointsOutput",
    )

    await repo.update_session(
        session_id,
        {
            f"{object_class}EndpointsJobId": str(job_id),
            f"{object_class}EndpointsInput": {
                "objectClass": object_class,
                "relevantDocumentationsCount": total_chunks,
                "baseApiUrl": base_api_url,
            },
        },
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
    db: AsyncSession = Depends(get_db),
):
    """
    Get the status of endpoints extraction job for the specified object class.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    jobId = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key=f"{object_class}EndpointsJobId",
        job_label="endpoints",
        not_found_detail=f"No endpoints job found for {object_class} in session {session_id}",
    )

    return await build_typed_job_status_response(jobId, EndpointResponse)


@router.put(
    "/{session_id}/classes/{object_class}/endpoints",
    summary="Override endpoints for object class",
)
async def override_class_endpoints(
    session_id: UUID = Path(..., description="Session ID"),
    object_class: str = Path(..., description="Object class name"),
    endpoints: Dict[str, Any] = Body(..., description="Endpoints data as JSON"),
    db: AsyncSession = Depends(get_db),
):
    """
    Manually override the endpoints for an object class.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    object_class = normalize_object_class_name(object_class)
    await repo.update_session(session_id, {f"{object_class}EndpointsOutput": endpoints})

    return {
        "message": f"Endpoints for {object_class} overridden successfully",
        "sessionId": session_id,
        "objectClass": object_class,
    }


# Digester Operations - Relations
@router.post(
    "/{session_id}/relations",
    response_model=JobCreateResponse,
    summary="Extract relations between object classes",
)
async def extract_relations(
    session_id: UUID = Path(..., description="Session ID"),
    use_previous_session_data: bool = Query(True, description="Whether to use previous session data if available"),
    db: AsyncSession = Depends(get_db),
):
    """
    Extract relations between object classes from documentation.
    Loads relevant object classes from session (where relevant=true).

    NOTE: We dont need to await documentation here, as it should have already been awaited during object class extraction.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    try:
        doc_items = await filter_documentation_items(DEFAULT_CRITERIA, session_id, db=db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=f"Session not found: {str(e)}")

    # Load object_classes from session
    relevant = await repo.get_session_data(session_id, "objectClassesOutput")
    if not relevant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No object classes found in session. Please run /classes endpoint first.",
        )

    job_id = await schedule_coroutine_job(
        job_type="digester.getRelations",
        input_payload={
            "documentationItems": doc_items,
            "relevantObjectClasses": relevant,
            "usePreviousSessionData": use_previous_session_data,
        },
        worker=service.extract_relations,
        worker_args=(doc_items, relevant),
        initial_stage="chunking",
        initial_message="Preparing and splitting documentation",
        session_id=session_id,
        session_result_key="relationsOutput",
    )

    await repo.update_session(
        session_id,
        {
            "relationsJobId": str(job_id),
            "relationsInput": {
                "relevantObjectClasses": relevant,
                "usePreviousSessionData": use_previous_session_data,
            },
        },
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/relations",
    response_model=JobStatusMultiDocResponse,
    summary="Get relations extraction status",
)
async def get_relations_status(
    session_id: UUID = Path(..., description="Session ID"),
    jobId: Optional[UUID] = Query(None, description="Job ID (optional)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the status of relations extraction job.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    jobId = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key="relationsJobId",
        job_label="relations",
    )

    return await build_typed_job_status_response(jobId, RelationsResponse)


@router.put(
    "/{session_id}/relations",
    summary="Override relations data",
)
async def override_relations(
    session_id: UUID = Path(..., description="Session ID"),
    relations: Dict[str, Any] = Body(..., description="Relations data as JSON"),
    db: AsyncSession = Depends(get_db),
):
    """
    Manually override the relations data.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    await repo.update_session(session_id, {"relationsOutput": relations})

    return {"message": "Relations overridden successfully", "sessionId": session_id}


# Digester Operations - Auth & Metadata
@router.post(
    "/{session_id}/auth",
    response_model=JobCreateResponse,
    summary="Extract authentication information",
)
async def extract_auth(
    session_id: UUID = Path(..., description="Session ID"),
    use_previous_session_data: bool = Query(True, description="Whether to use previous session data if available"),
    db: AsyncSession = Depends(get_db),
):
    """
    Extract authentication information from documentation.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    job_id = await schedule_coroutine_job(
        job_type="digester.getAuth",
        input_payload={"usePreviousSessionData": use_previous_session_data},
        dynamic_input_enabled=True,
        dynamic_input_provider=auth_input,
        worker=service.extract_auth,
        worker_kwargs={},
        initial_stage="chunking",
        initial_message="Preparing and splitting documentation",
        session_id=session_id,
        session_result_key="authOutput",
        await_documentation=True,
        await_documentation_timeout=750,
    )

    await repo.update_session(
        session_id,
        {
            "authJobId": str(job_id),
            "authInput": {
                "usePreviousSessionData": use_previous_session_data,
            },
        },
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/auth",
    response_model=JobStatusMultiDocResponse,
    summary="Get auth extraction status",
)
async def get_auth_status(
    session_id: UUID = Path(..., description="Session ID"),
    jobId: Optional[UUID] = Query(None, description="Job ID (optional)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the status of auth extraction job.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    jobId = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key="authJobId",
        job_label="auth",
    )

    return await build_typed_job_status_response(jobId, AuthResponse)


@router.post(
    "/{session_id}/metadata",
    response_model=JobCreateResponse,
    summary="Extract metadata information",
)
async def extract_metadata(
    session_id: UUID = Path(..., description="Session ID"),
    use_previous_session_data: bool = Query(True, description="Whether to use previous session data if available"),
    db: AsyncSession = Depends(get_db),
):
    """
    Extract API metadata from documentation.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    job_id = await schedule_coroutine_job(
        job_type="digester.getInfoMetadata",
        input_payload={"usePreviousSessionData": use_previous_session_data},
        dynamic_input_enabled=True,
        dynamic_input_provider=metadata_input,
        worker=service.extract_info_metadata,
        worker_kwargs={},
        initial_stage="chunking",
        initial_message="Preparing and splitting documentation",
        session_id=session_id,
        session_result_key="metadataOutput",
        await_documentation=True,
        await_documentation_timeout=750,
    )

    await repo.update_session(
        session_id,
        {
            "metadataJobId": str(job_id),
            "metadataInput": {
                "usePreviousSessionData": use_previous_session_data,
            },
        },
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/metadata",
    response_model=JobStatusMultiDocResponse,
    summary="Get metadata extraction status",
)
async def get_metadata_status(
    session_id: UUID = Path(..., description="Session ID"),
    jobId: Optional[UUID] = Query(None, description="Job ID (optional)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the status of metadata extraction job.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    jobId = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key="metadataJobId",
        job_label="metadata",
    )

    return await build_typed_job_status_response(jobId, InfoResponse)
