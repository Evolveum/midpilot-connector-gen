"""
Digester endpoints for V2 API (session-centric).
All digester operations are nested under sessions.
"""

# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...common.chunk_filter.filter import filter_documentation_items
from ...common.chunk_filter.schema import ChunkFilterCriteria
from ...common.database.config import get_db
from ...common.database.repositories.session_repository import SessionRepository
from ...common.enums import JobStatus
from ...common.jobs import get_job_status, schedule_coroutine_job
from ...common.schema import JobCreateResponse, JobStatusMultiDocResponse
from ...common.session.session import get_session_documentation
from . import service
from .schema import (
    AuthResponse,
    EndpointsResponse,
    InfoResponse,
    ObjectClassesResponse,
    ObjectClassSchemaResponse,
    RelationsResponse,
)

router = APIRouter()

DEFAULT_CRITERIA = ChunkFilterCriteria(  # Apply static category filter to documentation items
    min_length=None,
    min_endpoints_num=None,
    allowed_categories=[
        "spec_yaml",
        "spec_json",
        "reference_api",
    ],
)

AUTH_CRITERIA = ChunkFilterCriteria(
    min_length=None,
    min_endpoints_num=None,
    allowed_categories=[
        "spec_yaml",
        "spec_json",
        "overview",
    ],
    allowed_tags=[
        [
            "authentication",
            "auth",
            "authorization",
        ]
    ],
)

ENDPOINT_CRITERIA = ChunkFilterCriteria(
    min_length=None,
    min_endpoints_num=1,
    allowed_categories=[
        "spec_yaml",
        "spec_json",
        "reference_api",
    ],
)


# Helper Functions
async def _build_typed_job_status_response(job_id: UUID, model_cls) -> JobStatusMultiDocResponse:
    """Helper to normalize building a JobStatusMultiDocResponse and parsing the result into a given model."""
    status = await get_job_status(job_id)
    result_payload = None
    raw_status = status.get("status", JobStatus.not_found.value)
    if raw_status == JobStatus.finished.value and isinstance(status.get("result"), dict):
        try:
            result_dict = status["result"]
            # Handle new format with chunks metadata
            if "result" in result_dict and isinstance(result_dict["result"], dict):
                actual_result = result_dict["result"]
            else:
                actual_result = result_dict

            # Special handling for ObjectClassesResponse to ensure proper model validation
            if model_cls == ObjectClassesResponse:
                # Ensure objectClasses is a list and each item has the required fields
                if "objectClasses" in actual_result and isinstance(actual_result["objectClasses"], list):
                    for obj_class in actual_result["objectClasses"]:
                        # Ensure relevant_chunks exists and is a list
                        if "relevant_chunks" not in obj_class:
                            obj_class["relevant_chunks"] = []
                        elif not isinstance(obj_class["relevant_chunks"], list):
                            obj_class["relevant_chunks"] = []

            if hasattr(model_cls, "model_validate"):
                result_payload = model_cls.model_validate(actual_result)
            else:
                result_payload = model_cls(**actual_result)
        except Exception as e:
            return JobStatusMultiDocResponse(
                jobId=status.get("jobId", job_id),
                status=JobStatus.failed,
                errors=[f"Corrupted result payload: {str(e)}"],
            )
    enum_status = JobStatus(raw_status)

    return JobStatusMultiDocResponse(
        jobId=status.get("jobId", job_id),
        status=enum_status,
        createdAt=status.get("createdAt"),
        startedAt=status.get("startedAt"),
        updatedAt=status.get("updatedAt"),
        progress=status.get("progress"),
        result=result_payload,
        errors=status.get("errors"),
    )


async def object_classes_input(db: AsyncSession, session_id: UUID) -> Dict[str, Any]:
    """
    Dynamic input provider for object classes extraction job.
    It is important to wait for the documentation to be ready before starting the job.
    input:
        session_id - session ID to retrieve documentation items from
        db - SQLAlchemy AsyncSession
    output:
        dict with:
            sessionInput - dict with documentationItemsCount and totalLength - used for input in session field
            jobInput - dict for job input field
            args - tuple with documentation items
    """
    # Apply static category filter to documentation items
    doc_items = await filter_documentation_items(DEFAULT_CRITERIA, session_id, db=db)
    total_length = sum(len(item["content"]) for item in doc_items)
    return {
        "sessionInput": {
            "documentationItemsCount": len(doc_items),
            "totalLength": total_length,
        },
        "jobInput": {
            "documentationItems": doc_items,
        },
        "args": (doc_items,),
    }


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
    db: AsyncSession = Depends(get_db),
):
    """
    Extract object classes from documentation stored in or uploaded to the session.
    Optionally filter documentation items based on provided criteria.
    Returns jobId to poll for results.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    job_id = await schedule_coroutine_job(
        job_type="digester.getObjectClass",
        input_payload={},
        dynamic_input_enabled=True,
        dynamic_input_provider=object_classes_input,
        worker=service.extract_object_classes,
        worker_kwargs={
            "filter_relevancy": filter_relevancy,
            "min_relevancy_level": min_relevancy_level,
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
                # "documentationItemsCount": len(doc_items),
                # "totalLength": total_length,
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
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    if not jobId:
        jobId = await repo.get_session_data(session_id, "objectClassesJobId")
        if not jobId:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"No object classes job found in session {session_id}"
            )

    response = await _build_typed_job_status_response(jobId, ObjectClassesResponse)

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
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

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

    # Find the specific object class (case-insensitive)
    normalized_name = object_class.strip().lower()
    for obj_cls in object_classes:
        if isinstance(obj_cls, dict) and obj_cls.get("name", "").strip().lower() == normalized_name:
            # Merge in attributes and endpoints if they exist
            result = obj_cls.copy()

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
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

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
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    # Get existing object classes
    object_classes_output = await repo.get_session_data(session_id, "objectClassesOutput")
    if not object_classes_output or not isinstance(object_classes_output, dict):
        # Initialize with empty structure if none exists
        object_classes_output = {"objectClasses": []}

    object_classes = object_classes_output.get("objectClasses", [])
    if not isinstance(object_classes, list):
        object_classes = []

    # Ensure the name field is set from the URL path parameter
    object_class_data["name"] = object_class

    # Find and update existing object class (case-insensitive)
    normalized_name = object_class.strip().lower()
    updated = False
    for i, obj_cls in enumerate(object_classes):
        if isinstance(obj_cls, dict) and obj_cls.get("name", "").strip().lower() == normalized_name:
            # Update existing object class
            object_classes[i] = object_class_data
            updated = True
            break

    # If not found, add new object class
    if not updated:
        object_classes.append(object_class_data)

    # Update session
    object_classes_output["objectClasses"] = object_classes
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
    db: AsyncSession = Depends(get_db),
):
    """
    Extract attributes schema for a specific object class.
    Only processes chunks that are relevant to the object class (from relevantChunks).
    Updates both {object_class}AttributesOutput and the attributes field in the specific object class.

    NOTE: We dont need to await documentation here, as it should have already been awaited during object class extraction.
    """
    repo = SessionRepository(db)

    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    # Get the object class data to find relevant chunks
    object_classes_output = await repo.get_session_data(session_id, "objectClassesOutput")
    if not object_classes_output or not isinstance(object_classes_output, dict):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No object classes found in session {session_id}. Please run /classes endpoint first.",
        )

    # Find the specific object class (case-insensitive)
    object_classes = object_classes_output.get("objectClasses", [])
    normalized_name = object_class.strip().lower()
    target_object_class = None
    for obj_cls in object_classes:
        if isinstance(obj_cls, dict) and obj_cls.get("name", "").strip().lower() == normalized_name:
            target_object_class = obj_cls
            break

    if not target_object_class:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Object class '{object_class}' not found in session {session_id}.",
        )

    relevant_chunks = target_object_class.get("relevantChunks", [])
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
            "documentation_items": doc_items,
            "objectClass": object_class,
            "relevantChunks": relevant_chunks,
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
                "documentationItemsCount": len(doc_items),
                "relevantChunksCount": total_chunks,
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
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    if not jobId:
        jobId = await repo.get_session_data(session_id, f"{object_class}AttributesJobId")
        if not jobId:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No attributes job found for {object_class} in session {session_id}",
            )

    # Get job status but override result with current session data
    response = await _build_typed_job_status_response(jobId, ObjectClassSchemaResponse)

    # If job is finished, replace result with current session data (which may have been updated)
    if response.status == JobStatus.finished:
        attributes_output = await repo.get_session_data(session_id, f"{object_class}AttributesOutput")
        if attributes_output:
            try:
                # Validate and parse the session data
                response.result = ObjectClassSchemaResponse.model_validate(attributes_output)
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
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    object_class = object_class.strip().lower()
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
    db: AsyncSession = Depends(get_db),
):
    """
    Extract API endpoints for a specific object class.
    Automatically loads base API URL from session metadata if available.
    Updates both {object_class}EndpointsOutput and the endpoints field in the specific object class.
    Only processes chunks that are relevant to the object class (from relevantChunks).

    NOTE: We dont need to await documentation here, as it should have already been awaited during object class extraction.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    # Get the object class data to find relevant chunks
    object_classes_output = await repo.get_session_data(session_id, "objectClassesOutput")
    if not object_classes_output or not isinstance(object_classes_output, dict):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No object classes found in session {session_id}. Please run /classes endpoint first.",
        )

    # Find the specific object class (case-insensitive)
    object_classes = object_classes_output.get("objectClasses", [])
    normalized_name = object_class.strip().lower()
    target_object_class = None
    for obj_cls in object_classes:
        if isinstance(obj_cls, dict) and obj_cls.get("name", "").strip().lower() == normalized_name:
            target_object_class = obj_cls
            break

    if not target_object_class:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Object class '{object_class}' not found in session {session_id}.",
        )

    # relevant_chunks = target_object_class.get("relevantChunks", [])
    # relevant_chunks_from_object_class = target_object_class.get("relevantChunks", [])
    criteria = ENDPOINT_CRITERIA.model_copy()
    criteria.allowed_tags = [[object_class.lower().strip()], ["endpoint", "endpoints"]]
    relevant_chunks_full = await filter_documentation_items(criteria, session_id, db=db)

    # If we dont have relevant chunks with ENDPOINT_CRITERIA, try to find relevant chunks with DEFAULT_CRITERIA
    if not relevant_chunks_full:
        criteria = DEFAULT_CRITERIA.model_copy()
        relevant_chunks_full = await filter_documentation_items(criteria, session_id, db=db)

    relevant_chunks = [
        {"docUuid": chunk["uuid"]}
        for chunk in relevant_chunks_full
        # if chunk["uuid"] in {rc["docUuid"] for rc in relevant_chunks_from_object_class}
    ]
    if not relevant_chunks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No relevant chunks found for object class '{object_class}'. Cannot extract endpoints.",
        )

    # Get full documentation to extract relevant chunks
    doc_items = await get_session_documentation(session_id, db=db)

    # Load base API URL from session metadata
    base_api_url = ""
    metadata = await repo.get_session_data(session_id, "metadataOutput")
    if metadata and isinstance(metadata, dict):
        info_about_schema = metadata.get("infoAboutSchema", {})
        base_api_endpoints = info_about_schema.get("baseApiEndpoint", [])
        if base_api_endpoints and isinstance(base_api_endpoints, list) and len(base_api_endpoints) > 0:
            base_api_url = base_api_endpoints[0].get("uri", "")

    total_chunks = len(relevant_chunks)
    job_id = await schedule_coroutine_job(
        job_type="digester.getEndpoints",
        input_payload={
            "documentationItems": doc_items,
            "objectClass": object_class,
            "baseApiUrl": base_api_url,
            "relevantChunks": relevant_chunks,
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
                "documentationItemsCount": len(doc_items),
                "relevantChunksCount": total_chunks,
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
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    if not jobId:
        jobId = await repo.get_session_data(session_id, f"{object_class}EndpointsJobId")
        if not jobId:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No endpoints job found for {object_class} in session {session_id}",
            )

    return await _build_typed_job_status_response(jobId, EndpointsResponse)


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
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    object_class = object_class.strip().lower()
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
async def extract_relations(session_id: UUID = Path(..., description="Session ID"), db: AsyncSession = Depends(get_db)):
    """
    Extract relations between object classes from documentation.
    Loads relevant object classes from session (where relevant=true).

    NOTE: We dont need to await documentation here, as it should have already been awaited during object class extraction.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    try:
        doc_items = await filter_documentation_items(DEFAULT_CRITERIA, session_id, db=db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=f"Session not found: {str(e)}")
    total_length = sum(len(item["content"]) for item in doc_items)

    # Load object_classes from session
    relevant = await repo.get_session_data(session_id, "objectClassesOutput")
    if not relevant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No object classes found in session. Please run /classes endpoint first.",
        )

    job_id = await schedule_coroutine_job(
        job_type="digester.getRelations",
        input_payload={"documentationItems": doc_items, "relevantObjectClasses": relevant},
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
                "documentationItemsCount": len(doc_items),
                "totalLength": total_length,
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
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    if not jobId:
        jobId = await repo.get_session_data(session_id, "relationsJobId")
        if not jobId:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"No relations job found in session {session_id}"
            )

    return await _build_typed_job_status_response(jobId, RelationsResponse)


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
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    await repo.update_session(session_id, {"relationsOutput": relations})

    return {"message": "Relations overridden successfully", "sessionId": session_id}


async def auth_input(db: AsyncSession, session_id: UUID) -> Dict[str, Any]:
    """
    Dynamic input provider for auth extraction job.
    It is important to wait for the documentation to be ready before starting the job.
    input:
        session_id - session ID to retrieve documentation items from
        db - SQLAlchemy AsyncSession
    output:
        dict with:
            args - tuple of documentation items
            sessionInput - dict with documentationItemsCount and totalLength - used for input in session field
            jobInput - dict for job input field
    """
    # Apply static category filter to documentation items
    doc_items = await filter_documentation_items(AUTH_CRITERIA, session_id, db=db)
    total_length = sum(len(item["content"]) for item in doc_items)
    return {
        "sessionInput": {
            "documentationItemsCount": len(doc_items),
            "totalLength": total_length,
        },
        "jobInput": {
            "documentationItems": doc_items,
        },
        "args": (doc_items,),
    }


# Digester Operations - Auth & Metadata
@router.post(
    "/{session_id}/auth",
    response_model=JobCreateResponse,
    summary="Extract authentication information",
)
async def extract_auth(
    session_id: UUID = Path(..., description="Session ID"),
    db: AsyncSession = Depends(get_db),
):
    """
    Extract authentication information from documentation.
    """
    repo = SessionRepository(db)

    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    job_id = await schedule_coroutine_job(
        job_type="digester.getAuth",
        input_payload={},
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
            # "authInput": {"documentationItemsCount": len(doc_items), "totalLength": total_length},
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
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    if not jobId:
        jobId = await repo.get_session_data(session_id, "authJobId")
        if not jobId:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"No auth job found in session {session_id}"
            )

    return await _build_typed_job_status_response(jobId, AuthResponse)


async def metadata_input(db: AsyncSession, session_id: UUID) -> Dict[str, Any]:
    """
    Dynamic input provider for metadata extraction job.
    It is important to wait for the documentation to be ready before starting the job.
    input:
        session_id - session ID to retrieve documentation items from
        db - SQLAlchemy AsyncSession
    output:
        dict with:
            'args' key containing tuple of documentation items,
            'sessionInput' key with metadata for input in session field,
            'jobInput' key with metadata for input in job field
    """
    doc_items = await filter_documentation_items(DEFAULT_CRITERIA, session_id, db=db)
    total_length = sum(len(item["content"]) for item in doc_items)
    return {
        "sessionInput": {
            "documentationItemsCount": len(doc_items),
            "totalLength": total_length,
        },
        "jobInput": {
            "documentationItems": doc_items,
        },
        "args": (doc_items,),
    }


@router.post(
    "/{session_id}/metadata",
    response_model=JobCreateResponse,
    summary="Extract metadata information",
)
async def extract_metadata(
    session_id: UUID = Path(..., description="Session ID"),
    db: AsyncSession = Depends(get_db),
):
    """
    Extract API metadata from documentation.
    """
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    job_id = await schedule_coroutine_job(
        job_type="digester.getInfoMetadata",
        input_payload={},
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
            # "metadataInput": {"documentationItemsCount": len(doc_items), "totalLength": total_length},
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
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

    if not jobId:
        jobId = await repo.get_session_data(session_id, "metadataJobId")
        if not jobId:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"No metadata job found in session {session_id}"
            )

    return await _build_typed_job_status_response(jobId, InfoResponse)
