# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.chunk_filter.filter import filter_documentation_items
from src.common.database.config import get_db
from src.common.database.repositories.session_repository import SessionRepository
from src.common.enums import ApiType
from src.common.errors import ObjectClassesNotFoundError, SessionNotFoundError
from src.common.jobs import schedule_coroutine_job
from src.common.schema import JobCreateResponse, JobStatusMultiDocResponse
from src.common.session.session import ensure_session_exists, resolve_session_job_id
from src.common.utils.normalize import normalize_object_class_name
from src.common.utils.session_info_metadata import get_session_base_api_url
from src.common.utils.status_response import build_typed_job_status_response
from src.modules.digester import results, service
from src.modules.digester.inputs import (
    auth_input,
    connectivity_endpoint_input,
    metadata_input,
    object_classes_input,
)
from src.modules.digester.schemas import (
    AttributeResponse,
    AuthResponse,
    ConnectivityEndpointResponse,
    EndpointResponse,
    InfoResponse,
    ObjectClassesResponse,
    RelationsResponse,
)
from src.modules.digester.selection.criteria import DEFAULT_CRITERIA
from src.modules.digester.selection.documentation_selector import DocumentationSelector

router = APIRouter()
logger = logging.getLogger(__name__)


# Digester Operations - Object Classes
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

    input_payload: dict[str, Any] = {"skipCache": skip_cache}
    if api_type is not None:
        input_payload["apiType"] = api_type.value

    job_id = await schedule_coroutine_job(
        job_type="digester.getObjectClass",
        input_payload=input_payload,
        dynamic_input_enabled=True,
        dynamic_input_provider=object_classes_input,
        worker=service.extract_object_classes,
        worker_kwargs={
            "session_id": session_id,
            "api_type_override": api_type,
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
            "objectClassesInput": dict(input_payload),
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


# Digester Operations - Object Class Attributes
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
    db: AsyncSession = Depends(get_db),
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

    selection = await DocumentationSelector(db).build_attribute_plan(
        repo=repo,
        session_id=session_id,
        object_class=object_class,
        api_type_override=api_type,
    )

    total_chunks = len(selection.relevant_chunks)
    job_id = await schedule_coroutine_job(
        job_type="digester.getObjectClassSchema",
        input_payload={
            "documentationItems": selection.doc_items,
            "objectClass": object_class,
            "relevantDocumentations": selection.relevant_chunks,
            "skipCache": skip_cache,
        },
        worker=service.extract_attributes,
        worker_args=(selection.doc_items, object_class, session_id, selection.relevant_chunks),
        worker_kwargs={"api_type_override": api_type},
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
    db: AsyncSession = Depends(get_db),
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


# Digester Operations - Object Class Endpoints
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
    db: AsyncSession = Depends(get_db),
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

    selection = await DocumentationSelector(db).build_endpoint_plan(
        repo=repo,
        session_id=session_id,
        object_class=object_class,
        api_type_override=api_type,
    )

    total_chunks = len(selection.relevant_chunks)
    job_id = await schedule_coroutine_job(
        job_type="digester.getEndpoints",
        input_payload={
            "documentationItems": selection.doc_items,
            "objectClass": object_class,
            "baseApiUrl": selection.base_api_url,
            "relevantDocumentations": selection.relevant_chunks,
            "skipCache": skip_cache,
        },
        worker=service.extract_endpoints,
        worker_args=(selection.doc_items, object_class, session_id, selection.relevant_chunks),
        worker_kwargs={"base_api_url": selection.base_api_url, "api_type_override": api_type},
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
                "baseApiUrl": selection.base_api_url,
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
    db: AsyncSession = Depends(get_db),
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


# Digester Operations - Relations
@router.post(
    "/{session_id}/relations",
    response_model=JobCreateResponse,
    summary="Extract relations between object classes",
)
async def extract_relations(
    session_id: UUID = Path(..., description="Session ID"),
    skip_cache: bool = Query(False, alias="skipCache", description="Whether to skip cached data"),
    db: AsyncSession = Depends(get_db),
):
    """
    Extract relations between object classes from documentation.
    Loads object classes from session.

    NOTE: We dont need to await documentation here, as it should have already been awaited during object class extraction.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    try:
        doc_items = await filter_documentation_items(DEFAULT_CRITERIA, session_id, db=db)
    except ValueError as e:
        raise SessionNotFoundError(session_id) from e

    # Load object_classes from session
    relevant = await repo.get_session_data(session_id, "objectClassesOutput")
    if not relevant:
        raise ObjectClassesNotFoundError(session_id)

    job_id = await schedule_coroutine_job(
        job_type="digester.getRelations",
        input_payload={
            "documentationItems": doc_items,
            "relevantObjectClasses": relevant,
            "skipCache": skip_cache,
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
                "skipCache": skip_cache,
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

    resolved_job_id = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key="relationsJobId",
        job_label="relations",
    )

    return await build_typed_job_status_response(resolved_job_id, RelationsResponse)


@router.put(
    "/{session_id}/relations",
    summary="Override relations data",
)
async def override_relations(
    session_id: UUID = Path(..., description="Session ID"),
    relations: RelationsResponse = Body(..., description="Relations data as JSON"),
    db: AsyncSession = Depends(get_db),
):
    """
    Manually override the relations data.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    await repo.update_session(session_id, {"relationsOutput": relations.model_dump(by_alias=True, mode="json")})

    return {"message": "Relations overridden successfully", "sessionId": session_id}


# Digester Operations - Connectivity Endpoint
@router.post(
    "/{session_id}/connectivity-endpoint",
    response_model=JobCreateResponse,
    summary="Extract connectivity test endpoint",
)
async def extract_connectivity_endpoint(
    session_id: UUID = Path(..., description="Session ID"),
    skip_cache: bool = Query(False, alias="skipCache", description="Whether to skip cached data"),
    db: AsyncSession = Depends(get_db),
):
    """
    Extract one documented endpoint suitable for testing connectivity to the target application.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    base_api_url = await get_session_base_api_url(session_id)
    job_id = await schedule_coroutine_job(
        job_type="digester.getConnectivityEndpoint",
        input_payload={
            "baseApiUrl": base_api_url,
            "skipCache": skip_cache,
        },
        dynamic_input_enabled=True,
        dynamic_input_provider=connectivity_endpoint_input,
        worker=service.extract_connectivity_endpoint,
        worker_kwargs={
            "session_id": session_id,
            "base_api_url": base_api_url,
        },
        initial_stage="chunking",
        initial_message="Preparing documentation for connectivity endpoint extraction",
        session_id=session_id,
        session_result_key="connectivityEndpointOutput",
        await_documentation=True,
        await_documentation_timeout=750,
    )

    await repo.update_session(
        session_id,
        {
            "connectivityEndpointJobId": str(job_id),
            "connectivityEndpointInput": {
                "baseApiUrl": base_api_url,
                "skipCache": skip_cache,
            },
        },
    )

    return JobCreateResponse(jobId=job_id)


@router.get(
    "/{session_id}/connectivity-endpoint",
    response_model=JobStatusMultiDocResponse,
    summary="Get connectivity endpoint extraction status",
)
async def get_connectivity_endpoint_status(
    session_id: UUID = Path(..., description="Session ID"),
    jobId: Optional[UUID] = Query(None, description="Job ID (optional)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the status of connectivity endpoint extraction job.
    Returns current session data when the job has finished, including manual overrides.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    resolved_job_id = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key="connectivityEndpointJobId",
        job_label="connectivity endpoint",
    )

    response = await build_typed_job_status_response(resolved_job_id, ConnectivityEndpointResponse)
    return await results.refresh_connectivity_endpoint_status(db, repo, response, session_id)


@router.put(
    "/{session_id}/connectivity-endpoint",
    summary="Override connectivity test endpoint",
)
async def override_connectivity_endpoint(
    session_id: UUID = Path(..., description="Session ID"),
    connectivity_endpoint: ConnectivityEndpointResponse = Body(..., description="Connectivity endpoint payload"),
    db: AsyncSession = Depends(get_db),
):
    """
    Manually override the selected connectivity endpoint in the session.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    payload = connectivity_endpoint.model_dump(by_alias=True, mode="json")
    await results.store_connectivity_endpoint_override(db, repo, session_id, payload)

    return {"message": "Connectivity endpoint overridden successfully", "sessionId": session_id}


# Digester Operations - Auth & Metadata
@router.post(
    "/{session_id}/auth",
    response_model=JobCreateResponse,
    summary="Extract authentication information",
)
async def extract_auth(
    session_id: UUID = Path(..., description="Session ID"),
    skip_cache: bool = Query(False, alias="skipCache", description="Whether to skip cached data"),
    db: AsyncSession = Depends(get_db),
):
    """
    Extract authentication information from documentation.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    job_id = await schedule_coroutine_job(
        job_type="digester.getAuth",
        input_payload={"skipCache": skip_cache},
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
                "skipCache": skip_cache,
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

    resolved_job_id = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key="authJobId",
        job_label="auth",
    )

    return await build_typed_job_status_response(resolved_job_id, AuthResponse)


@router.post(
    "/{session_id}/metadata",
    response_model=JobCreateResponse,
    summary="Extract metadata information",
)
async def extract_metadata(
    session_id: UUID = Path(..., description="Session ID"),
    skip_cache: bool = Query(False, alias="skipCache", description="Whether to skip cached data"),
    db: AsyncSession = Depends(get_db),
):
    """
    Extract API metadata from documentation.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    job_id = await schedule_coroutine_job(
        job_type="digester.getInfoMetadata",
        input_payload={"skipCache": skip_cache},
        dynamic_input_enabled=True,
        dynamic_input_provider=metadata_input,
        worker=service.extract_info_metadata,
        worker_kwargs={"session_id": session_id},
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
                "skipCache": skip_cache,
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

    resolved_job_id = await resolve_session_job_id(
        repo,
        session_id,
        jobId,
        session_key="metadataJobId",
        job_label="metadata",
    )

    return await build_typed_job_status_response(resolved_job_id, InfoResponse)


@router.put(
    "/{session_id}/metadata",
    summary="Restore metadata information",
)
async def restore_metadata(
    session_id: UUID = Path(..., description="Session ID"),
    metadata: InfoResponse = Body(..., description="Info metadata payload as JSON"),
    db: AsyncSession = Depends(get_db),
):
    """
    Restore metadataOutput in session from provided infoMetadata payload.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)

    await repo.update_session(session_id, {"metadataOutput": metadata.model_dump(by_alias=True)})

    return {"message": "Metadata updated successfully", "sessionId": session_id}
