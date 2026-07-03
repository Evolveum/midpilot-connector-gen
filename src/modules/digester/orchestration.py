# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Request/job orchestration for digester extraction operations.

Sits between the thin HTTP router and the digester extraction workers under
``extractors/``. It owns request-scoped job scheduling: selecting documentation,
assembling scheduler and worker payloads, and persisting the resulting job id
and session input metadata.

The router remains responsible for HTTP request parsing, path/query
normalization, and session existence checks. The extractor workers remain
responsible for actual extraction, LLM calls, and protocol-specific logic.
"""

from typing import Any, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.common.chunk_filter.filter import filter_documentation_items
from src.common.database.repositories.session_repository import SessionRepository
from src.common.enums import ApiType
from src.common.errors import ObjectClassesNotFoundError, SessionNotFoundError
from src.common.jobs import persist_job_pointer, schedule_coroutine_job
from src.common.utils.session_info_metadata import get_session_base_api_url
from src.modules.digester.extractors.attributes import extract_attributes
from src.modules.digester.extractors.auth import extract_auth
from src.modules.digester.extractors.connectivity_endpoint import extract_connectivity_endpoint
from src.modules.digester.extractors.endpoints import extract_endpoints
from src.modules.digester.extractors.info import extract_info_metadata
from src.modules.digester.extractors.object_class import extract_object_classes
from src.modules.digester.extractors.rest.relations import extract_relations
from src.modules.digester.selection import (
    DEFAULT_CRITERIA,
    DocumentationSelector,
    auth_input,
    build_object_class_extraction_input,
    connectivity_endpoint_input,
    metadata_input,
)

_DOCUMENTATION_WAIT_TIMEOUT_SECONDS = 750


async def schedule_object_class_extraction(
    *,
    repo: SessionRepository,
    session_id: UUID,
    skip_cache: bool,
    api_type: Optional[ApiType],
) -> UUID:
    """
    Schedule object-class extraction and persist ``objectClassesJobId`` /
    ``objectClassesInput`` in the session.
    """
    input_payload: dict[str, Any] = {"skipCache": skip_cache}
    if api_type is not None:
        input_payload["apiType"] = api_type.value

    job_id = await schedule_coroutine_job(
        job_type="digester.getObjectClass",
        input_payload=input_payload,
        dynamic_input_enabled=True,
        dynamic_input_provider=build_object_class_extraction_input,
        worker=extract_object_classes,
        worker_kwargs={
            "session_id": session_id,
            "api_type_override": api_type,
        },
        initial_stage="chunking",
        initial_message="Preparing and splitting documentation",
        session_id=session_id,
        session_result_key="objectClassesOutput",
        await_documentation=True,
        await_documentation_timeout=_DOCUMENTATION_WAIT_TIMEOUT_SECONDS,
    )

    await persist_job_pointer(repo, session_id, "objectClasses", dict(input_payload), job_id)
    return job_id


async def schedule_attribute_extraction(
    *,
    db: AsyncSession,
    repo: SessionRepository,
    session_id: UUID,
    object_class: str,
    skip_cache: bool,
    api_type: Optional[ApiType],
) -> UUID:
    """
    Schedule attribute extraction for one normalized object class and persist
    ``{object_class}AttributesJobId`` / ``{object_class}AttributesInput``.
    """
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
        worker=extract_attributes,
        worker_args=(selection.doc_items, object_class, session_id, selection.relevant_chunks),
        worker_kwargs={"api_type_override": api_type},
        initial_stage="chunking",
        initial_message=f"Processing {total_chunks} relevant chunks for {object_class}",
        session_id=session_id,
        session_result_key=f"{object_class}AttributesOutput",
    )

    await persist_job_pointer(
        repo,
        session_id,
        f"{object_class}Attributes",
        {"objectClass": object_class, "relevantDocumentationsCount": total_chunks},
        job_id,
    )
    return job_id


async def schedule_endpoint_extraction(
    *,
    db: AsyncSession,
    repo: SessionRepository,
    session_id: UUID,
    object_class: str,
    skip_cache: bool,
    api_type: Optional[ApiType],
) -> UUID:
    """
    Schedule endpoint extraction for one normalized object class and persist
    ``{object_class}EndpointsJobId`` / ``{object_class}EndpointsInput``.
    """
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
        worker=extract_endpoints,
        worker_args=(selection.doc_items, object_class, session_id, selection.relevant_chunks),
        worker_kwargs={"base_api_url": selection.base_api_url, "api_type_override": api_type},
        initial_stage="chunking",
        initial_message=f"Processing {total_chunks} relevant chunks for {object_class}",
        session_id=session_id,
        session_result_key=f"{object_class}EndpointsOutput",
    )

    await persist_job_pointer(
        repo,
        session_id,
        f"{object_class}Endpoints",
        {
            "objectClass": object_class,
            "relevantDocumentationsCount": total_chunks,
            "baseApiUrl": selection.base_api_url,
        },
        job_id,
    )
    return job_id


async def schedule_relations_extraction(
    *,
    db: AsyncSession,
    repo: SessionRepository,
    session_id: UUID,
    skip_cache: bool,
) -> UUID:
    """
    Schedule relation extraction and persist ``relationsJobId`` /
    ``relationsInput``.
    """
    try:
        doc_items = await filter_documentation_items(DEFAULT_CRITERIA, session_id, db=db)
    except ValueError as e:
        raise SessionNotFoundError(session_id) from e

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
        worker=extract_relations,
        worker_args=(doc_items, relevant),
        initial_stage="chunking",
        initial_message="Preparing and splitting documentation",
        session_id=session_id,
        session_result_key="relationsOutput",
    )

    await persist_job_pointer(
        repo,
        session_id,
        "relations",
        {"relevantObjectClasses": relevant, "skipCache": skip_cache},
        job_id,
    )
    return job_id


async def schedule_connectivity_endpoint_extraction(
    *,
    repo: SessionRepository,
    session_id: UUID,
    skip_cache: bool,
) -> UUID:
    """
    Schedule connectivity-endpoint extraction and persist
    ``connectivityEndpointJobId`` / ``connectivityEndpointInput``.
    """
    base_api_url = await get_session_base_api_url(session_id)
    job_id = await schedule_coroutine_job(
        job_type="digester.getConnectivityEndpoint",
        input_payload={
            "baseApiUrl": base_api_url,
            "skipCache": skip_cache,
        },
        dynamic_input_enabled=True,
        dynamic_input_provider=connectivity_endpoint_input,
        worker=extract_connectivity_endpoint,
        worker_kwargs={
            "session_id": session_id,
            "base_api_url": base_api_url,
        },
        initial_stage="chunking",
        initial_message="Preparing documentation for connectivity endpoint extraction",
        session_id=session_id,
        session_result_key="connectivityEndpointOutput",
        await_documentation=True,
        await_documentation_timeout=_DOCUMENTATION_WAIT_TIMEOUT_SECONDS,
    )

    await persist_job_pointer(
        repo,
        session_id,
        "connectivityEndpoint",
        {"baseApiUrl": base_api_url, "skipCache": skip_cache},
        job_id,
    )
    return job_id


async def schedule_auth_extraction(
    *,
    repo: SessionRepository,
    session_id: UUID,
    skip_cache: bool,
) -> UUID:
    """Schedule auth extraction and persist ``authJobId`` / ``authInput``."""
    job_id = await schedule_coroutine_job(
        job_type="digester.getAuth",
        input_payload={"skipCache": skip_cache},
        dynamic_input_enabled=True,
        dynamic_input_provider=auth_input,
        worker=extract_auth,
        worker_kwargs={},
        initial_stage="chunking",
        initial_message="Preparing and splitting documentation",
        session_id=session_id,
        session_result_key="authOutput",
        await_documentation=True,
        await_documentation_timeout=_DOCUMENTATION_WAIT_TIMEOUT_SECONDS,
    )

    await persist_job_pointer(repo, session_id, "auth", {"skipCache": skip_cache}, job_id)
    return job_id


async def schedule_metadata_extraction(
    *,
    repo: SessionRepository,
    session_id: UUID,
    skip_cache: bool,
) -> UUID:
    """Schedule metadata extraction and persist ``metadataJobId`` / ``metadataInput``."""
    job_id = await schedule_coroutine_job(
        job_type="digester.getInfoMetadata",
        input_payload={"skipCache": skip_cache},
        dynamic_input_enabled=True,
        dynamic_input_provider=metadata_input,
        worker=extract_info_metadata,
        worker_kwargs={},
        initial_stage="chunking",
        initial_message="Preparing and splitting documentation",
        session_id=session_id,
        session_result_key="metadataOutput",
        await_documentation=True,
        await_documentation_timeout=_DOCUMENTATION_WAIT_TIMEOUT_SECONDS,
    )

    await persist_job_pointer(repo, session_id, "metadata", {"skipCache": skip_cache}, job_id)
    return job_id
