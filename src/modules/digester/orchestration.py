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

from src.database.repositories.session_repository import SessionRepository
from src.documents.filtering.filter import filter_documentation_items
from src.jobs import job_input_reference, persist_job_pointer, schedule_coroutine_job
from src.modules.digester.errors import EndpointExtractionNotSupportedError, ObjectClassesNotFoundError
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
from src.session.info_metadata import get_session_base_api_url, resolve_effective_api_type
from src.shared.enums import ApiType, GenerationIntent
from src.shared.job_types import JobType
from src.shared.normalize import DOCUMENTATION_SELECTION_INPUT_KEY
from src.shared.session_keys import (
    AUTH,
    CONNECTIVITY_ENDPOINT,
    METADATA,
    OBJECT_CLASSES,
    RELATIONS,
    attributes_keys,
    endpoints_keys,
)

_DOCUMENTATION_WAIT_TIMEOUT_SECONDS = 750


async def schedule_object_class_extraction(
    *,
    repo: SessionRepository,
    session_id: UUID,
    skip_cache: bool,
    api_type: Optional[ApiType],
    intent: Optional[GenerationIntent] = None,
) -> UUID:
    """
    Schedule object-class extraction and persist ``objectClassesJobId`` /
    ``objectClassesInput`` in the session.

    ``intent`` is the business-domain lens (management, itsm, or management_itsm) used
    to prioritize object classes; it defaults to ``management`` when omitted. Unlike
    ``apiType``, the resolved value is always recorded on ``objectClassesInput`` (not
    only when explicitly passed) so every new job pointer states which intent produced
    it.
    """
    effective_intent = intent or GenerationIntent.MANAGEMENT
    input_payload: dict[str, Any] = {"skipCache": skip_cache, "intent": effective_intent.value}
    if api_type is not None:
        input_payload["apiType"] = api_type.value

    job_id = await schedule_coroutine_job(
        db=repo.db,
        job_type=JobType.DIGESTER_OBJECT_CLASSES,
        input_payload=input_payload,
        dynamic_input_enabled=True,
        dynamic_input_provider=build_object_class_extraction_input,
        worker=extract_object_classes,
        worker_kwargs={
            "session_id": session_id,
            "api_type_override": api_type,
            "intent": effective_intent,
        },
        initial_stage="chunking",
        initial_message="Preparing and splitting documentation",
        session_id=session_id,
        session_result_key=OBJECT_CLASSES.output,
        await_documentation=True,
        await_documentation_timeout=_DOCUMENTATION_WAIT_TIMEOUT_SECONDS,
    )

    await persist_job_pointer(repo, session_id, OBJECT_CLASSES, dict(input_payload), job_id)
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

    The job input stores the documentation selection (primary, fallback and SCIM
    baseline, or the SQL schema) and the effective protocol, so the worker never
    reloads the session's documentation.
    """
    keys = attributes_keys(object_class)
    protocol = await resolve_effective_api_type(session_id, api_type)
    plan = await DocumentationSelector(db).build_attribute_plan(
        repo=repo,
        session_id=session_id,
        object_class=object_class,
        protocol=protocol,
    )

    job_id = await schedule_coroutine_job(
        db=repo.db,
        job_type=JobType.DIGESTER_ATTRIBUTES,
        input_payload={
            "objectClass": object_class,
            "apiType": protocol.value,
            DOCUMENTATION_SELECTION_INPUT_KEY: plan.selection.to_job_input(),
            "skipCache": skip_cache,
        },
        worker=extract_attributes,
        worker_kwargs={
            "selection": job_input_reference(DOCUMENTATION_SELECTION_INPUT_KEY),
            "object_class": object_class,
            "session_id": session_id,
            "protocol": protocol,
        },
        initial_stage="chunking",
        initial_message=f"Processing {plan.relevant_chunk_count} relevant chunks for {object_class}",
        session_id=session_id,
        session_result_key=keys.output,
    )

    await persist_job_pointer(
        repo,
        session_id,
        keys,
        {"objectClass": object_class, "relevantDocumentationsCount": plan.relevant_chunk_count},
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

    A SQL session is rejected before a job is created: a database connector has no
    endpoints, so there is nothing to extract and nothing downstream consumes the result.
    """
    keys = endpoints_keys(object_class)
    protocol = await resolve_effective_api_type(session_id, api_type)
    if protocol == ApiType.SQL:
        raise EndpointExtractionNotSupportedError(object_class, protocol.value)

    plan = await DocumentationSelector(db).build_endpoint_plan(
        repo=repo,
        session_id=session_id,
        object_class=object_class,
        protocol=protocol,
        api_type_override=api_type,
    )

    job_id = await schedule_coroutine_job(
        db=repo.db,
        job_type=JobType.DIGESTER_ENDPOINTS,
        input_payload={
            "objectClass": object_class,
            "apiType": protocol.value,
            "objectClassFlags": plan.object_class_flags,
            "baseApiUrl": plan.base_api_url,
            DOCUMENTATION_SELECTION_INPUT_KEY: plan.selection.to_job_input(),
            "skipCache": skip_cache,
        },
        worker=extract_endpoints,
        worker_kwargs={
            "selection": job_input_reference(DOCUMENTATION_SELECTION_INPUT_KEY),
            "object_class": object_class,
            "session_id": session_id,
            "protocol": protocol,
            "base_api_url": job_input_reference("baseApiUrl"),
            "object_class_flags": job_input_reference("objectClassFlags"),
        },
        initial_stage="chunking",
        initial_message=f"Processing {plan.relevant_chunk_count} relevant chunks for {object_class}",
        session_id=session_id,
        session_result_key=keys.output,
    )

    await persist_job_pointer(
        repo,
        session_id,
        keys,
        {
            "objectClass": object_class,
            "objectClassFlags": plan.object_class_flags,
            "relevantDocumentationsCount": plan.relevant_chunk_count,
            "baseApiUrl": plan.base_api_url,
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
    doc_items = await filter_documentation_items(DEFAULT_CRITERIA, session_id, db=db)

    relevant = await repo.get_session_data(session_id, OBJECT_CLASSES.output)
    if not relevant:
        raise ObjectClassesNotFoundError(session_id)

    job_id = await schedule_coroutine_job(
        db=repo.db,
        job_type=JobType.DIGESTER_RELATIONS,
        input_payload={
            "documentationItems": doc_items,
            "relevantObjectClasses": relevant,
            "skipCache": skip_cache,
        },
        worker=extract_relations,
        worker_args=(
            job_input_reference("documentationItems"),
            job_input_reference("relevantObjectClasses"),
        ),
        initial_stage="chunking",
        initial_message="Preparing and splitting documentation",
        session_id=session_id,
        session_result_key=RELATIONS.output,
    )

    # The object classes travel in the job input; the pointer keeps request metadata only.
    await persist_job_pointer(repo, session_id, RELATIONS, {"skipCache": skip_cache}, job_id)
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
        db=repo.db,
        job_type=JobType.DIGESTER_CONNECTIVITY_ENDPOINT,
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
        session_result_key=CONNECTIVITY_ENDPOINT.output,
        await_documentation=True,
        await_documentation_timeout=_DOCUMENTATION_WAIT_TIMEOUT_SECONDS,
    )

    await persist_job_pointer(
        repo,
        session_id,
        CONNECTIVITY_ENDPOINT,
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
        db=repo.db,
        job_type=JobType.DIGESTER_AUTH,
        input_payload={"skipCache": skip_cache},
        dynamic_input_enabled=True,
        dynamic_input_provider=auth_input,
        worker=extract_auth,
        worker_kwargs={},
        initial_stage="chunking",
        initial_message="Preparing and splitting documentation",
        session_id=session_id,
        session_result_key=AUTH.output,
        await_documentation=True,
        await_documentation_timeout=_DOCUMENTATION_WAIT_TIMEOUT_SECONDS,
    )

    await persist_job_pointer(repo, session_id, AUTH, {"skipCache": skip_cache}, job_id)
    return job_id


async def schedule_metadata_extraction(
    *,
    repo: SessionRepository,
    session_id: UUID,
    skip_cache: bool,
) -> UUID:
    """Schedule metadata extraction and persist ``metadataJobId`` / ``metadataInput``."""
    job_id = await schedule_coroutine_job(
        db=repo.db,
        job_type=JobType.DIGESTER_INFO_METADATA,
        input_payload={"skipCache": skip_cache},
        dynamic_input_enabled=True,
        dynamic_input_provider=metadata_input,
        worker=extract_info_metadata,
        worker_kwargs={},
        initial_stage="chunking",
        initial_message="Preparing and splitting documentation",
        session_id=session_id,
        session_result_key=METADATA.output,
        await_documentation=True,
        await_documentation_timeout=_DOCUMENTATION_WAIT_TIMEOUT_SECONDS,
    )

    await persist_job_pointer(repo, session_id, METADATA, {"skipCache": skip_cache}, job_id)
    return job_id
