# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Request/job orchestration for per-object-class codegen operations.

Sits between the thin HTTP router and the codegen worker functions in
``service``. It owns the request-scoped flow shared by the search/create/
update/delete endpoints: loading attributes and endpoints from the session,
resolving the effective protocol, assembling job/worker/session payloads,
scheduling the coroutine job, and persisting the resulting job id.

This module may depend on the session repository and the job scheduler and may
reference ``service`` workers; the deeper ``core`` LLM engine must not.
"""

from typing import Any, Awaitable, Callable, Mapping, Optional
from uuid import UUID

from src.common.database.repositories.session_repository import SessionRepository
from src.common.enums import ApiType
from src.common.errors import AttributesNotFoundError, OperationSurfaceNotFoundError
from src.common.jobs import schedule_coroutine_job
from src.common.utils.session_info_metadata import resolve_effective_api_type
from src.modules.codegen.schema import CodegenOperationInput, CodegenRepairContext

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
