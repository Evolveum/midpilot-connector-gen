# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Request/job orchestration for codegen operations.

Sits between the thin HTTP router and the codegen generation workers in
``generation``. It owns the request-scoped flow behind every ``generate_*``
endpoint: loading inputs from the session, resolving the effective protocol,
assembling job/worker/session payloads, scheduling the coroutine job, and
persisting the resulting job id.

This module may depend on the session repository and the job scheduler and may
reference ``generation`` workers; the deeper ``core`` LLM engine must not. It
raises domain errors (``AppError`` subclasses) rather than HTTP exceptions so
the HTTP layer stays in the router / exception handlers.
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable, Iterable, Mapping, Optional, cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import config
from src.database.repositories.session_repository import SessionRepository
from src.documents.chunking import count_tokens
from src.documents.relevance import hydrate_auth_sequences_from_relevance
from src.jobs import job_input_reference, persist_job_pointer, schedule_coroutine_job
from src.modules.codegen import connector_fix, generation
from src.modules.codegen.errors import (
    ConnectorFixContextTooLargeError,
    ConnectorScriptsNotFoundError,
    InvalidConnectorScriptOverrideError,
    UnknownConnectorOperationError,
)
from src.modules.codegen.schema import (
    AuthorizationCodegenInput,
    CodegenOperationInput,
    CodegenRepairContext,
    ConnectorFixInput,
)
from src.modules.codegen.selection.artifact_catalog import ConnectorArtifact, load_connector_artifacts
from src.modules.codegen.selection.authorization import enrich_preferred_authorizations
from src.modules.codegen.utils.groovy_validation import GroovyValidationError, ensure_valid_groovy_code
from src.modules.digester.errors import (
    AttributesNotFoundError,
    InvalidRelationsOutputError,
    OperationSurfaceNotFoundError,
    RelationNotFoundError,
    RelationsNotFoundError,
    SqlPhysicalSchemaNotFoundError,
)
from src.modules.digester.schemas import RelationsResponse
from src.session.info_metadata import resolve_effective_api_type
from src.shared.enums import ApiType

logger = logging.getLogger(__name__)

# Shared preparing-stage metadata for the search/create/update/delete jobs.
_INITIAL_STAGE = "preparing"
_INITIAL_MESSAGE = "Preparing code generation from relevant chunks"


_PROTOCOLS_REQUIRING_ENDPOINTS = frozenset({ApiType.REST})


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
    preferred_endpoints = codegen_input.preferred_endpoints_payload() if codegen_input is not None else None
    repair_context = codegen_input.repair_context() if codegen_input is not None else None
    context_payload = codegen_input.context_payload() if codegen_input is not None else {}

    eps = await repo.get_session_data(session_id, f"{object_class}EndpointsOutput")
    if eps is None and protocol in _PROTOCOLS_REQUIRING_ENDPOINTS:
        raise OperationSurfaceNotFoundError(object_class, session_id)

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
        "attributes": job_input_reference("attributes"),
        "session_id": session_id,
        "object_class": object_class,
        "preferred_endpoints": (job_input_reference("preferredEndpoints") if preferred_endpoints is not None else None),
        "protocol": protocol,
    }
    worker_kwargs.update(extra_worker_kwargs or {})
    if repair_context is not None:
        worker_kwargs["repair_context"] = repair_context
    if eps is not None:
        job_input["endpoints"] = eps
        worker_kwargs["endpoints"] = job_input_reference("endpoints")

    job_id = await schedule_coroutine_job(
        db=repo.db,
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

    await persist_job_pointer(repo, session_id, key_prefix, session_input, job_id)

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

    input_preferred_authorizations = codegen_input.preferred_authorizations_payload()

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
            logger.warning(
                "[Codegen:Authorization] Could not hydrate auth relevance; "
                "scheduling with the stored auth output instead",
                exc_info=True,
            )

    preferred_authorizations = enrich_preferred_authorizations(auth_output, input_preferred_authorizations)
    repair_context = codegen_input.repair_context()
    context_payload = codegen_input.context_payload()

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
        "auth_payload": job_input_reference("auth"),
        "preferred_authorizations": (
            job_input_reference("preferredAuthorizations") if preferred_authorizations is not None else None
        ),
        "session_id": session_id,
        "protocol": protocol,
    }
    if repair_context is not None:
        worker_kwargs["repair_context"] = repair_context

    job_id = await schedule_coroutine_job(
        db=repo.db,
        job_type="codegen.getAuthorization",
        input_payload=job_input,
        worker=generation.generate_authorization_code,
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
    await persist_job_pointer(repo, session_id, "authorization", session_input, job_id)

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
    if protocol == ApiType.SQL:
        sql_context = attrs.get("sqlContext")
        if not isinstance(sql_context, Mapping) or not isinstance(sql_context.get("physicalTable"), Mapping):
            raise SqlPhysicalSchemaNotFoundError(object_class, stale_payload=True)
    repair_context = codegen_input.repair_context() if codegen_input is not None else None
    context_payload = codegen_input.context_payload() if codegen_input is not None else {}
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
        db=repo.db,
        job_type="codegen.getNativeSchema",
        input_payload=job_input,
        worker=generation.generate_native_schema_code,
        worker_args=(job_input_reference("attributes"), object_class),
        worker_kwargs=worker_kwargs,
        initial_stage="queue",
        initial_message="Queued code generation",
        session_id=session_id,
        session_result_key=f"{object_class}NativeSchemaOutput",
    )

    await persist_job_pointer(
        repo,
        session_id,
        f"{object_class}NativeSchema",
        {"attributes": attrs, "objectClass": object_class, **context_payload},
        job_id,
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

    repair_context = codegen_input.repair_context() if codegen_input is not None else None
    context_payload = codegen_input.context_payload() if codegen_input is not None else {}
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
        db=repo.db,
        job_type="codegen.getConnID",
        input_payload=job_input,
        worker=generation.generate_conn_id_code,
        worker_args=(job_input_reference("attributes"), object_class),
        worker_kwargs=worker_kwargs,
        initial_stage="queue",
        initial_message="Queued code generation",
        session_id=session_id,
        session_result_key=f"{object_class}ConnidOutput",
    )

    await persist_job_pointer(
        repo,
        session_id,
        f"{object_class}Connid",
        {"attributes": attrs, "objectClass": object_class, **context_payload},
        job_id,
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
        db=repo.db,
        job_type="codegen.getRelation",
        input_payload={
            "relations": relations_payload,
            "relationName": relation_name,
            "sessionId": session_id,
            "skipCache": skip_cache,
        },
        worker=generation.generate_relation_code,
        worker_kwargs={
            "relations": job_input_reference("relations"),
            "relation_name": relation_name,
            "session_id": session_id,
        },
        initial_stage="preparing",
        initial_message="Queued code generation from relevant chunks",
        session_id=session_id,
        session_result_key=f"{relation_name}CodeOutput",
    )

    await persist_job_pointer(repo, session_id, f"{relation_name}Code", {"relations": relations_payload}, job_id)

    return job_id


async def schedule_connector_fix_job(
    *,
    repo: SessionRepository,
    session_id: UUID,
    object_class: str,
    api_type: Optional[ApiType],
    codegen_input: ConnectorFixInput,
) -> UUID:
    """
    Schedule a connector fix for all generated scripts of one object class.

    Loads the selected object's generated scripts and its extracted schema, validates and
    applies caller overrides for this run only, enforces the input budget, schedules the job and
    persists the class-scoped ``{objectClass}ConnectorFixJobId`` / ``{objectClass}ConnectorFixInput``
    pointer.

    The budget is checked twice for a request that carries overrides, and the two checks
    answer different questions: the first rejects an oversized request body before the Groovy
    parser or the session is touched at all, the second measures the exact text the job will
    carry once the overrides are normalized and merged with the stored scripts.

    Unlike the per-operation jobs this one passes no ``session_result_key``: it
    publishes many ``{key}Output`` rows itself, and ``schedule_coroutine_job``
    accepts only one.
    """
    await _enforce_fix_token_budget(override.code for override in codegen_input.scripts)
    protocol = await resolve_effective_api_type(session_id, api_type)

    stored_artifacts = await load_connector_artifacts(repo, session_id, object_class)
    if not stored_artifacts:
        raise ConnectorScriptsNotFoundError(session_id, object_class)

    validated_overrides = await _validate_script_overrides(codegen_input)
    artifacts = _apply_script_overrides(stored_artifacts, validated_overrides, session_id)
    await _enforce_fix_token_budget(artifact.code for artifact in artifacts)

    # The fix must not decide attribute naming from the generated scripts alone: they are
    # exactly what is under suspicion. The extracted schema is the authority, so it travels
    # with the job the same way it does for every generation job.
    attributes = await repo.get_session_data(session_id, f"{object_class}AttributesOutput")
    if not attributes:
        raise AttributesNotFoundError(object_class, session_id)
    endpoints = await repo.get_session_data(session_id, f"{object_class}EndpointsOutput")

    job_input: dict[str, Any] = {
        "sessionId": session_id,
        "objectClass": object_class,
        "scripts": [artifact.to_payload() for artifact in artifacts],
        "midpointErrors": codegen_input.midpoint_errors,
        "attributes": attributes,
        "apiType": protocol.value,
        "skipCache": True,
    }

    worker_kwargs: dict[str, Any] = {
        "scripts": job_input_reference("scripts"),
        "midpoint_errors": job_input_reference("midpointErrors"),
        "attributes": job_input_reference("attributes"),
        "session_id": session_id,
        "protocol": protocol,
    }

    # Endpoints are optional on purpose: a SQL session has no endpoint surface, and the fix
    # must not fail for a protocol that never had one.
    if endpoints is not None:
        job_input["endpoints"] = endpoints
        worker_kwargs["endpoints"] = job_input_reference("endpoints")

    job_id = await schedule_coroutine_job(
        db=repo.db,
        job_type="codegen.fixConnector",
        input_payload=job_input,
        worker=connector_fix.fix_connector_code,
        worker_args=(),
        worker_kwargs=worker_kwargs,
        initial_stage=_INITIAL_STAGE,
        initial_message=f"Preparing {object_class} connector fix from stored operation scripts",
        session_id=session_id,
    )

    await persist_job_pointer(
        repo,
        session_id,
        f"{object_class}ConnectorFix",
        {
            "objectClass": object_class,
            "midpointErrors": codegen_input.midpoint_errors,
            # Preserve exactly the script overrides uploaded by the caller. Scripts
            # loaded from session storage remain only in the job input.
            "scripts": [script.model_dump(by_alias=True, mode="json") for script in codegen_input.scripts],
            "operationKeys": [artifact.operation_key for artifact in artifacts],
            "overriddenOperationKeys": [override.operation_key for override in codegen_input.scripts],
            "apiType": protocol.value,
        },
        job_id,
    )

    return job_id


def _apply_script_overrides(
    artifacts: list[ConnectorArtifact],
    overrides: Mapping[str, str],
    session_id: UUID,
) -> list[ConnectorArtifact]:
    """Replace stored code with caller-supplied code for this run only."""
    if not overrides:
        return artifacts

    known_keys = {artifact.operation_key for artifact in artifacts}
    for operation_key in overrides:
        if operation_key not in known_keys:
            raise UnknownConnectorOperationError(operation_key, session_id)

    return [
        artifact.with_code(overrides[artifact.operation_key]) if artifact.operation_key in overrides else artifact
        for artifact in artifacts
    ]


async def _validate_script_overrides(codegen_input: ConnectorFixInput) -> dict[str, str]:
    """Normalize and parse caller scripts in a worker thread after size checks pass."""
    if not codegen_input.scripts:
        return {}

    def validate_all() -> dict[str, str]:
        validated: dict[str, str] = {}
        for override in codegen_input.scripts:
            try:
                validated[override.operation_key] = ensure_valid_groovy_code(override.code)
            except GroovyValidationError as exc:
                raise InvalidConnectorScriptOverrideError(override.operation_key, str(exc)) from exc
        return validated

    return await asyncio.to_thread(validate_all)


async def _enforce_fix_token_budget(codes: Iterable[str]) -> None:
    """
    Reject Groovy too large to fix in one call.

    Never truncate: a partially seen connector produces confidently wrong
    cross-operation fixes. Tokenization is CPU-bound, so it stays off the request
    event loop just like Groovy parsing. An empty set of scripts is not measured,
    so a request without overrides does not pay for a pre-check of nothing.
    """
    scripts = list(codes)
    if not scripts:
        return

    input_tokens = await asyncio.to_thread(count_tokens, "\n\n".join(scripts))
    limit = config.codegen.fix_max_input_tokens
    if input_tokens > limit:
        raise ConnectorFixContextTooLargeError(input_tokens=input_tokens, limit=limit)
