# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Request/job orchestration for codegen operations.

Sits between the thin HTTP router and the codegen worker functions in
``service``. It owns the request-scoped flow behind every ``generate_*``
endpoint: loading inputs from the session, resolving the effective protocol,
assembling job/worker/session payloads, scheduling the coroutine job, and
persisting the resulting job id.

This module may depend on the session repository and the job scheduler and may
reference ``service`` workers; the deeper ``core`` LLM engine must not. It
raises domain errors (``AppError`` subclasses) rather than HTTP exceptions so
the HTTP layer stays in the router / exception handlers.
"""

from typing import Any, Awaitable, Callable, Mapping, Optional, cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database.repositories.session_repository import SessionRepository
from src.common.enums import ApiType
from src.common.errors import (
    AttributesNotFoundError,
    InvalidRelationsOutputError,
    OperationSurfaceNotFoundError,
    RelationNotFoundError,
    RelationsNotFoundError,
)
from src.common.jobs import schedule_coroutine_job
from src.common.utils.relevance import hydrate_auth_sequences_from_relevance
from src.common.utils.session_info_metadata import resolve_effective_api_type
from src.modules.codegen import service
from src.modules.codegen.schema import (
    AuthorizationCodegenInput,
    CodegenOperationInput,
    CodegenRepairContext,
)
from src.modules.codegen.selection.authorization import enrich_preferred_authorizations
from src.modules.digester.schemas import RelationsResponse

# Shared preparing-stage metadata for the search/create/update/delete jobs.
_INITIAL_STAGE = "preparing"
_INITIAL_MESSAGE = "Preparing code generation from relevant chunks"


def preferred_endpoints_from_input(
    codegen_input: Optional[CodegenOperationInput],
) -> Optional[list[dict]]:
    if codegen_input is None or not codegen_input.preferred_endpoints:
        return None
    return [endpoint.model_dump() for endpoint in codegen_input.preferred_endpoints]


def repair_context_from_input(
    codegen_input: Optional[CodegenRepairContext],
) -> Optional[CodegenRepairContext]:
    if codegen_input is None or not codegen_input.is_repair:
        return None
    return CodegenRepairContext(
        current_script=codegen_input.current_script,
        midpoint_errors=codegen_input.midpoint_errors,
    )


def context_payload_from_input(codegen_input: Optional[CodegenRepairContext]) -> dict:
    if codegen_input is None or not codegen_input.is_repair:
        return {}
    return CodegenRepairContext(
        current_script=codegen_input.current_script,
        midpoint_errors=codegen_input.midpoint_errors,
    ).to_payload()


def preferred_authorizations_from_input(
    codegen_input: Optional[AuthorizationCodegenInput],
) -> Optional[list[dict]]:
    if codegen_input is None or not codegen_input.preferred_authorizations:
        return None
    return [authorization.model_dump(exclude_none=True) for authorization in codegen_input.preferred_authorizations]


def missing_operation_surface_detail(protocol: ApiType, object_class: str, session_id: UUID) -> str:
    if protocol == ApiType.SQL:
        return (
            f"No SQL table metadata found for {object_class} in session {session_id}. "
            "Please run the table/schema extraction step for this object class first."
        )
    return (
        f"No endpoints found for {object_class} in session {session_id}. "
        f"Please run /classes/{object_class}/endpoints endpoint first."
    )


# This part is for codegen Create/Update/Delete/Search
async def schedule_operation_job(
    *,
    repo: SessionRepository,
    session_id: UUID,
    object_class: str,
    skip_cache: bool,
    api_type: Optional[ApiType],
    codegen_input: Optional[CodegenOperationInput],
    key_prefix: str,
    job_type: str,
    worker: Callable[..., Awaitable[Any]],
    extra_job_input: Optional[Mapping[str, Any]] = None,
    extra_worker_kwargs: Optional[Mapping[str, Any]] = None,
    extra_session_input: Optional[Mapping[str, Any]] = None,
) -> UUID:
    """
    Schedule a per-object-class codegen job (search/create/update/delete).

    Loads attributes and the operation surface (endpoints) from the session,
    resolves the effective protocol, assembles the job/worker/session payloads,
    schedules the coroutine job, and persists ``{key_prefix}JobId`` /
    ``{key_prefix}Input``. The job result is stored under ``{key_prefix}Output``.

    ``object_class`` must already be normalized and the session must already be
    known to exist; callers own those request-bootstrap concerns.

    The ``extra_*`` mappings carry operation-specific fields (e.g. ``intent``
    for search) that are merged into the base payloads.
    """
    attrs = await repo.get_session_data(session_id, f"{object_class}AttributesOutput")
    if not attrs:
        raise AttributesNotFoundError(object_class, session_id)

    protocol = await resolve_effective_api_type(session_id, api_type)
    preferred_endpoints = preferred_endpoints_from_input(codegen_input)
    repair_context = repair_context_from_input(codegen_input)
    context_payload = context_payload_from_input(codegen_input)

    eps = await repo.get_session_data(session_id, f"{object_class}EndpointsOutput")
    if eps is None and protocol != ApiType.SCIM:
        raise OperationSurfaceNotFoundError(missing_operation_surface_detail(protocol, object_class, session_id))

    job_input: dict[str, Any] = {
        "sessionId": session_id,
        "attributes": attrs,
        "object_class": object_class,
        "skipCache": skip_cache,
        "apiType": protocol.value,
    }
    job_input.update(extra_job_input or {})
    job_input.update(context_payload)
    if preferred_endpoints is not None:
        job_input["preferredEndpoints"] = preferred_endpoints

    worker_kwargs: dict[str, Any] = {
        "attributes": attrs,
        "session_id": session_id,
        "object_class": object_class,
        "preferred_endpoints": preferred_endpoints,
        "protocol": protocol,
    }
    worker_kwargs.update(extra_worker_kwargs or {})
    if repair_context is not None:
        worker_kwargs["repair_context"] = repair_context
    if eps is not None:
        job_input["endpoints"] = eps
        worker_kwargs["endpoints"] = eps

    job_id = await schedule_coroutine_job(
        job_type=job_type,
        input_payload=job_input,
        worker=worker,
        worker_args=(),
        worker_kwargs=worker_kwargs,
        initial_stage=_INITIAL_STAGE,
        initial_message=_INITIAL_MESSAGE,
        session_id=session_id,
        session_result_key=f"{key_prefix}Output",
    )

    session_input: dict[str, Any] = {"objectClass": object_class, "attributes": attrs}
    session_input.update(extra_session_input or {})
    session_input.update(context_payload)
    if eps is not None:
        session_input["endpoints"] = eps
    if preferred_endpoints is not None:
        session_input["preferredEndpoints"] = preferred_endpoints

    await repo.update_session(
        session_id,
        {
            f"{key_prefix}JobId": str(job_id),
            f"{key_prefix}Input": session_input,
        },
    )

    return job_id


async def schedule_authorization_job(
    *,
    db: AsyncSession,
    repo: SessionRepository,
    session_id: UUID,
    api_type: Optional[ApiType],
    skip_cache: bool,
    codegen_input: AuthorizationCodegenInput,
) -> UUID:
    """
    Schedule the connector-level authorization codegen job.

    Loads and (best-effort) hydrates the stored auth output, enriches preferred
    authorizations, schedules the job, and persists ``authorizationJobId`` /
    ``authorizationInput``.
    """
    protocol = await resolve_effective_api_type(session_id, api_type)

    input_preferred_authorizations = preferred_authorizations_from_input(codegen_input)

    auth_output_raw = await repo.get_session_data(session_id, "authOutput")
    if not isinstance(auth_output_raw, Mapping) or not auth_output_raw:
        auth_output: Mapping[str, Any] = {"auth": []}
    else:
        auth_output = cast(Mapping[str, Any], auth_output_raw)
        try:
            auth_output = cast(
                Mapping[str, Any],
                await hydrate_auth_sequences_from_relevance(db, session_id, auth_output),
            )
        except Exception:
            pass

    preferred_authorizations = enrich_preferred_authorizations(auth_output, input_preferred_authorizations)
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

    return job_id


async def schedule_native_schema_job(
    *,
    repo: SessionRepository,
    session_id: UUID,
    object_class: str,
    api_type: Optional[ApiType],
    skip_cache: bool,
    codegen_input: Optional[CodegenRepairContext],
) -> UUID:
    """
    Schedule the native-schema codegen job for an object class.

    Loads attributes, resolves the protocol, schedules the job, and persists
    ``{object_class}NativeSchemaJobId`` / ``{object_class}NativeSchemaInput``.
    """
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

    return job_id


async def schedule_connid_job(
    *,
    repo: SessionRepository,
    session_id: UUID,
    object_class: str,
    skip_cache: bool,
    codegen_input: Optional[CodegenRepairContext],
) -> UUID:
    """
    Schedule the ConnID codegen job for an object class.

    Loads attributes, schedules the job, and persists ``{object_class}ConnidJobId``
    / ``{object_class}ConnidInput``.
    """
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

    return job_id


async def schedule_relation_job(
    *,
    repo: SessionRepository,
    session_id: UUID,
    relation_name: str,
    skip_cache: bool,
) -> UUID:
    """
    Schedule the relation codegen job.

    Loads and validates the stored relations, selects the requested relation,
    schedules the job, and persists ``{relation_name}CodeJobId`` /
    ``{relation_name}CodeInput``.
    """
    relations_json = await repo.get_session_data(session_id, "relationsOutput")
    if not relations_json:
        raise RelationsNotFoundError(session_id)

    try:
        relations_model = RelationsResponse.model_validate(relations_json)
    except ValidationError as exc:
        raise InvalidRelationsOutputError(session_id) from exc

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

    return job_id
