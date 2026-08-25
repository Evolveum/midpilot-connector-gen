# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Worker for an object-class connector fix.

Takes one object class's CRUD/search/schema Groovy scripts plus the errors
midPoint reported, asks the model which scripts are at fault, validates and
merges what comes back, and writes the changed scripts to the session as one unit.

Named ``connector_fix`` rather than ``fix`` because ``repair`` already exists in
this package and the two are different things: repair rewrites one operation from
its own prompt suffix, this fixes all generated operations of one object class.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Sequence
from uuid import UUID

from src.core.db import async_session_maker
from src.core.errors import LLMUnavailableError
from src.database.repositories.documentation_repository import DocumentationRepository
from src.documents.chunking import normalize_to_text
from src.jobs import append_job_error, update_job_progress
from src.modules.codegen.core.fix_connector import run_connector_fix_pass
from src.modules.codegen.enums import ArtifactKind, SearchIntent
from src.modules.codegen.errors import (
    ConnectorFixContextTooLargeError,
    ConnectorFixEscalationFailedError,
    ConnectorFixProducedNoValidScriptError,
)
from src.modules.codegen.persistence import store_fixed_connector_scripts
from src.modules.codegen.schema import (
    ConnectorFixChange,
    ConnectorFixLLMResponse,
    ConnectorFixRejection,
    ConnectorFixResult,
    ConnectorScript,
    GroovyCodePayload,
)
from src.modules.codegen.selection.artifact_catalog import ConnectorArtifact, resolve_artifact_docs_paths
from src.modules.codegen.selection.docs_loader import load_required_adoc_text
from src.modules.codegen.selection.relevant_chunks import collect_connector_relevant_chunks
from src.modules.codegen.utils.groovy_validation import normalize_groovy_code, validate_groovy_code
from src.session.info_metadata import get_session_connection_target
from src.shared.content_types import is_conndev_documentation_item
from src.shared.enums import ApiType, JobStage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AcceptedScript:
    """A proposed script that passed validation, with the model's reason for it."""

    code: str
    reason: str


_DOCUMENTATIONS_PACKAGE = "src.modules.codegen.documentations"


async def fix_connector_code(
    *,
    scripts: Sequence[Mapping[str, Any]],
    midpoint_errors: Sequence[str],
    session_id: UUID,
    job_id: UUID,
    protocol: ApiType,
) -> ConnectorFixResult:
    """
    Fix the selected connector scripts from the errors midPoint reported for them.

    ``scripts`` are the artifact payloads assembled by orchestration (stored code,
    with any caller-supplied overrides already applied).

    :raises ConnectorFixEscalationFailedError: when the documentation pass fails and no first-pass repair is usable
    :raises ConnectorFixProducedNoValidScriptError: when every proposed script was rejected
    """
    artifacts = [ConnectorArtifact(**_payload_to_kwargs(payload)) for payload in scripts]
    artifact_payloads = [artifact.to_payload() for artifact in artifacts]
    by_operation_key = {artifact.operation_key: artifact for artifact in artifacts}

    await update_job_progress(
        job_id,
        stage=JobStage.generating,
        message=f"Analyzing {len(artifacts)} connector script(s) against {len(midpoint_errors)} midPoint error(s)",
    )

    base_api_url, database_name = await get_session_connection_target(session_id, protocol=protocol)
    connection_target = base_api_url or database_name
    dsl_documentation = _load_dsl_documentation(artifacts, protocol)

    async def run_pass(
        *,
        documentation_query: str | None = None,
        documentation_chunks: str = "",
        previous_attempt: ConnectorFixLLMResponse | None = None,
    ) -> ConnectorFixLLMResponse | None:
        return await run_connector_fix_pass(
            artifact_payloads=artifact_payloads,
            midpoint_errors=midpoint_errors,
            protocol=protocol,
            connection_target=connection_target,
            dsl_documentation=dsl_documentation,
            job_id=job_id,
            documentation_query=documentation_query,
            documentation_chunks=documentation_chunks,
            previous_attempt=previous_attempt,
        )

    # The first pass sees all original scripts and may propose fixes. It can also
    # ask for application documentation. When it asks with a query and relevant
    # documentation is available, the second pass sees the same original scripts,
    # the documentation, and a summary of the first pass. It does not receive the
    # first-pass fixed code as script input. After a successful second pass, its
    # proposal replaces the first one for the same operation; other first-pass
    # proposals are kept.
    response = await run_pass()
    escalated = False
    escalation_failed = False
    escalation_error: ConnectorFixContextTooLargeError | LLMUnavailableError | None = None
    documentation_query: str | None = None

    # One escalation round, expressed as a straight line rather than a loop: the cap
    # is structural, so it cannot drift into repeated documentation requests.
    if response is not None and response.needs_documentation:
        documentation_query = (response.documentation_query or "").strip()
        if not documentation_query:
            logger.warning("[Codegen:Fix] Documentation requested without a query; keeping the first-pass result")
        else:
            documentation = await _load_escalation_documentation(session_id, artifacts, job_id)
            if not documentation:
                logger.warning("[Codegen:Fix] No documentation available for the requested context")
            else:
                await update_job_progress(
                    job_id,
                    stage=JobStage.generating,
                    message="Retrying the fix with the requested application documentation",
                )
                logger.info("[Codegen:Fix] Escalating to session documentation: %s", documentation_query)
                second: ConnectorFixLLMResponse | None = None
                try:
                    second = await run_pass(
                        documentation_query=documentation_query,
                        documentation_chunks=documentation,
                        previous_attempt=response,
                    )
                except (ConnectorFixContextTooLargeError, LLMUnavailableError) as exc:
                    escalation_failed = True
                    escalation_error = exc
                else:
                    escalation_failed = second is None
                if not escalation_failed and second is not None:
                    response = _merge_fix_responses(response, second)
                    escalated = True
                    if second.needs_documentation:
                        logger.warning("[Codegen:Fix] Second pass requested more documentation; escalation cap reached")

    if response is None:
        return _unchanged_result(artifacts, analysis="The fix could not be completed; see the job errors.")

    accepted, rejections, unusable_count = await _validate_proposed_scripts(response, by_operation_key, job_id)

    if escalation_failed and not accepted:
        if escalation_error is not None:
            raise escalation_error
        raise ConnectorFixEscalationFailedError()

    if not accepted and unusable_count:
        # Every script the model proposed was unusable. Finishing "successfully" with
        # zero effect would read as a completed fix. A script that merely matched the
        # stored one is not unusable, so it does not reach here.
        raise ConnectorFixProducedNoValidScriptError(unusable_count)

    if escalation_failed:
        failure_detail = str(escalation_error) if escalation_error is not None else "no usable structured response"
        logger.warning(
            "[Codegen:Fix] Documentation pass failed; using %d valid first-pass repair(s): %s",
            len(accepted),
            failure_detail,
        )
        await append_job_error(
            job_id,
            f"[Codegen:Fix] Documentation pass failed; using {len(accepted)} valid first-pass repair(s). "
            f"Reason: {failure_detail}",
        )

    if accepted:
        await store_fixed_connector_scripts(
            session_id,
            {
                by_operation_key[key].session_key: GroovyCodePayload.model_construct(code=accepted_script.code)
                for key, accepted_script in accepted.items()
            },
            job_id=job_id,
        )
    else:
        logger.info("[Codegen:Fix] No script needed changing; session left untouched")

    return ConnectorFixResult(
        scripts=[
            ConnectorScript(
                operationKey=artifact.operation_key,
                sessionKey=artifact.session_key,
                code=(accepted[artifact.operation_key].code if artifact.operation_key in accepted else artifact.code),
            )
            for artifact in artifacts
        ],
        changedOperations=[
            ConnectorFixChange(operationKey=operation_key, reason=accepted_script.reason)
            for operation_key, accepted_script in accepted.items()
        ],
        rejectedScripts=rejections,
        documentationEscalated=escalated,
        documentationQuery=documentation_query,
        analysis=response.analysis,
    )


def _merge_fix_responses(
    first: ConnectorFixLLMResponse,
    second: ConnectorFixLLMResponse,
) -> ConnectorFixLLMResponse:
    """Keep first-pass fixes unless the documentation pass replaces the operation."""
    merged = {script.operation_key.strip().lower(): script for script in first.fixed_scripts}
    for script in second.fixed_scripts:
        merged[script.operation_key.strip().lower()] = script

    return ConnectorFixLLMResponse(
        fixed_scripts=list(merged.values()),
        needs_documentation=second.needs_documentation,
        documentation_query=second.documentation_query,
        analysis=second.analysis if second.analysis is not None else first.analysis,
    )


async def _validate_proposed_scripts(
    response: ConnectorFixLLMResponse,
    by_operation_key: Mapping[str, ConnectorArtifact],
    job_id: UUID,
) -> tuple[Dict[str, AcceptedScript], List[ConnectorFixRejection], int]:
    """
    Keep the proposed scripts that are usable; report the rest.

    One unusable script must not sink the others, and an operation key the
    connector does not have never becomes a new session row.

    :return: (accepted by resolved operation key, rejections, count of *unusable*
        proposals - a proposal identical to the stored script is reported but is
        not counted as unusable, because nothing failed)
    """
    candidates: List[tuple[str, str, str]] = []
    rejections: List[ConnectorFixRejection] = []
    unusable_count = 0

    for script in response.fixed_scripts:
        operation_key = _resolve_operation_key(script.operation_key, by_operation_key)
        if operation_key is None:
            rejections.append(
                ConnectorFixRejection(
                    operationKey=script.operation_key,
                    reason="No such operation in this connector.",
                )
            )
            unusable_count += 1
            continue
        candidates.append((operation_key, script.code, script.reason))

    # groovy-parser is CPU-bound; validating a full object class inline would stall the loop.
    validation_errors = await asyncio.to_thread(
        lambda: [validate_groovy_code(code) for _, code, _ in candidates],
    )

    accepted: Dict[str, AcceptedScript] = {}
    for (operation_key, code, reason), validation_error in zip(candidates, validation_errors):
        if validation_error is not None:
            rejections.append(
                ConnectorFixRejection(operationKey=operation_key, reason=f"Invalid Groovy: {validation_error}")
            )
            unusable_count += 1
            continue
        normalized = normalize_groovy_code(code)
        if normalized == normalize_groovy_code(by_operation_key[operation_key].code):
            rejections.append(
                ConnectorFixRejection(
                    operationKey=operation_key, reason="Proposed script is identical to the stored one."
                )
            )
            continue
        accepted[operation_key] = AcceptedScript(code=normalized, reason=reason)

    for rejection in rejections:
        message = f"[Codegen:Fix] Rejected proposed script for {rejection.operation_key}: {rejection.reason}"
        logger.warning(
            "[Codegen:Fix] Rejected proposed script for %s: %s",
            rejection.operation_key,
            rejection.reason,
        )
        await append_job_error(job_id, message)

    return accepted, rejections, unusable_count


def _resolve_operation_key(proposed: str, by_operation_key: Mapping[str, ConnectorArtifact]) -> str | None:
    """Exact match first, then one case-insensitive lookup; object-class keys are lowercased."""
    candidate = proposed.strip()
    if candidate in by_operation_key:
        return candidate
    lowered = candidate.lower()
    for operation_key in by_operation_key:
        if operation_key.lower() == lowered:
            logger.info("[Codegen:Fix] Matched proposed key %s to %s by case", candidate, operation_key)
            return operation_key
    return None


def _load_dsl_documentation(artifacts: Sequence[ConnectorArtifact], protocol: ApiType) -> str:
    sections: List[str] = []
    for docs_path in resolve_artifact_docs_paths(artifacts, protocol):
        sections.append(f"== {docs_path}\n\n{load_required_adoc_text(_DOCUMENTATIONS_PACKAGE, docs_path)}")
    return "\n\n".join(sections)


async def _load_escalation_documentation(
    session_id: UUID,
    artifacts: Sequence[ConnectorArtifact],
    job_id: UUID,
) -> str:
    """Materialize all chunks already known to be relevant for the artifacts' object class."""
    object_classes = list(dict.fromkeys(a.object_class for a in artifacts if a.object_class))
    pairs = await collect_connector_relevant_chunks(session_id, object_classes)
    if not pairs:
        return ""

    # Keep every selected chunk. This first version intentionally applies no
    # ranking or documentation-only cap; the complete prompt safety limit is
    # still enforced before the second LLM call.
    ordered_chunk_ids: List[UUID] = []
    for pair in pairs:
        raw_chunk_id = pair.get("chunk_id") or pair.get("chunkId")
        try:
            ordered_chunk_ids.append(UUID(str(raw_chunk_id)))
        except (TypeError, ValueError):
            continue

    async with async_session_maker() as db:
        documentation_items = await DocumentationRepository(db).get_documentation_items_by_chunk_ids(
            session_id,
            ordered_chunk_ids,
        )

    contents = {
        item["chunkId"]: item.get("content")
        for item in documentation_items
        if isinstance(item.get("chunkId"), str) and not is_conndev_documentation_item(item)
    }

    sections: List[str] = []
    for chunk_id in ordered_chunk_ids:
        content = contents.get(str(chunk_id))
        if isinstance(content, str) and content.strip():
            sections.append(normalize_to_text(content))

    if not sections:
        await append_job_error(job_id, "[Codegen:Fix] Requested documentation had no usable content")
    return "\n\n---\n\n".join(sections)


def _unchanged_result(artifacts: Sequence[ConnectorArtifact], *, analysis: str) -> ConnectorFixResult:
    return ConnectorFixResult(
        scripts=[
            ConnectorScript(operationKey=artifact.operation_key, sessionKey=artifact.session_key, code=artifact.code)
            for artifact in artifacts
        ],
        analysis=analysis,
    )


def _payload_to_kwargs(payload: Mapping[str, Any]) -> Dict[str, Any]:
    intent = payload.get("intent")
    return {
        "operation_key": payload["operationKey"],
        "kind": ArtifactKind(payload["kind"]),
        "object_class": payload.get("objectClass"),
        "intent": SearchIntent(intent) if intent else None,
        "code": payload["code"],
    }
