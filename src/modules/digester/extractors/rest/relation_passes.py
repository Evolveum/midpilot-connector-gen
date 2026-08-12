# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
The LLM stages of REST relation detection.

Each function here is one pass with one prompt. They are kept separate from the pipeline
in :mod:`relations` so that the pipeline reads as a sequence of stages and each stage can
be exercised on its own.

Every pass goes through :func:`invoke_extraction_chain_with_retry`, so a transient LLM
failure costs a retry rather than the evidence it was about to produce.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Sequence, cast
from uuid import UUID

from src.core.llm import build_structured_chain, raise_if_llm_unavailable
from src.jobs import append_job_error
from src.modules.digester.extraction.chunk_extraction import (
    build_chunk_extraction_chain,
    extract_single_chunk,
    invoke_extraction_chain_with_retry,
)
from src.modules.digester.extractors.rest.relation_context import LOG_SCOPE
from src.modules.digester.prompts.rest.relations_prompts import (
    get_relation_adjudication_system_prompt,
    get_relation_adjudication_user_prompt,
    get_relation_class_sweep_system_prompt,
    get_relation_class_sweep_user_prompt,
    get_relation_harvest_system_prompt,
    get_relation_harvest_user_prompt,
    get_relation_pair_focus_system_prompt,
    get_relation_pair_focus_user_prompt,
    get_relation_verification_system_prompt,
    get_relation_verification_user_prompt,
)
from src.modules.digester.schemas.relation_analysis import (
    RelationObservation,
    RelationObservationsResponse,
    RelationPairJudgement,
    RelationRefutation,
)

logger = logging.getLogger(__name__)

LOGGER_PREFIX = f"[{LOG_SCOPE}] "


def build_harvest_chain() -> Any:
    """One reusable chain for the per-chunk harvest pass."""
    return build_chunk_extraction_chain(
        pydantic_model=RelationObservationsResponse,
        system_prompt=get_relation_harvest_system_prompt,
        user_prompt=get_relation_harvest_user_prompt,
    )


def build_class_sweep_chain() -> Any:
    """One reusable chain for the per-class sweep pass."""
    return build_structured_chain(
        get_relation_class_sweep_system_prompt,
        get_relation_class_sweep_user_prompt,
        RelationObservationsResponse,
        user_role="human",
    )


def build_pair_focus_chain() -> Any:
    """One reusable chain for the focused pair re-read pass."""
    return build_structured_chain(
        get_relation_pair_focus_system_prompt,
        get_relation_pair_focus_user_prompt,
        RelationObservationsResponse,
        user_role="human",
    )


def build_adjudication_chain() -> Any:
    """One reusable chain for the pair adjudication pass."""
    return build_structured_chain(
        get_relation_adjudication_system_prompt,
        get_relation_adjudication_user_prompt,
        RelationPairJudgement,
        user_role="human",
    )


def build_verification_chain() -> Any:
    """One reusable chain for the adversarial verification pass."""
    return build_structured_chain(
        get_relation_verification_system_prompt,
        get_relation_verification_user_prompt,
        RelationRefutation,
        user_role="human",
    )


async def harvest_chunk(
    *,
    content: str,
    job_id: UUID,
    chunk_id: Optional[UUID],
    chunk_metadata: Optional[Dict[str, Any]],
    object_classes_json: str,
    chain: Any,
) -> tuple[List[RelationObservation], bool]:
    """
    Stage 1: report every class-to-class link one documentation fragment supports.

    Recall-first on purpose. Acceptance needs the whole class pair in view, which a single
    fragment never has, so nothing is rejected at this point.
    """

    def parse_fn(result: RelationObservationsResponse) -> List[RelationObservation]:
        return list(result.observations or [])

    observations, has_relevant_data = await extract_single_chunk(
        schema=content,
        pydantic_model=RelationObservationsResponse,
        system_prompt=get_relation_harvest_system_prompt,
        user_prompt=get_relation_harvest_user_prompt,
        parse_fn=parse_fn,
        job_id=job_id,
        logger_prefix=f"{LOGGER_PREFIX}[Harvest] ",
        chunk_id=chunk_id,
        chunk_metadata=chunk_metadata,
        extra_llm_attrs={"object_classes": object_classes_json},
        extraction_chain=chain,
    )
    return observations, has_relevant_data


async def _invoke_pass(
    *,
    chain: Any,
    payload: Dict[str, Any],
    run_name: str,
    job_id: UUID,
    failure_context: str,
) -> Any:
    """Run one non-chunk relation pass, converting a failure into a job error rather than a crash.

    A single pair failing must not lose the other pairs, so the exception is recorded and
    the caller continues with what it has. An unreachable LLM still propagates.
    """
    try:
        return await invoke_extraction_chain_with_retry(
            chain,
            payload,
            logger_prefix=f"[{LOG_SCOPE}:{run_name}] ",
        )
    except Exception as exc:
        raise_if_llm_unavailable(exc, context="extracting relations")
        logger.exception("%s%s", LOGGER_PREFIX, failure_context)
        await append_job_error(job_id, f"{LOGGER_PREFIX}{failure_context}: {exc}")
        return None


async def sweep_class(
    *,
    focus_class: str,
    focus_description: str,
    object_classes_json: str,
    documentation: str,
    job_id: UUID,
    chain: Any,
) -> List[RelationObservation]:
    """
    Stage 3: ask what one object class relates to, with all of that class's documentation in view.

    Iterating classes rather than chunks matches how API documentation is organized, and is
    what recovers links whose two ends are described in different fragments.
    """
    result = await _invoke_pass(
        chain=chain,
        payload={
            "focus_class": focus_class,
            "focus_description": focus_description,
            "object_classes": object_classes_json,
            "documentation": documentation,
        },
        run_name="ClassSweep",
        job_id=job_id,
        failure_context=f"Class sweep failed for {focus_class}",
    )
    if not isinstance(result, RelationObservationsResponse):
        return []
    return list(result.observations or [])


async def focus_pair(
    *,
    class_a: str,
    class_b: str,
    class_metadata: Sequence[Dict[str, Any]],
    known_observations: Sequence[RelationObservation],
    documentation: str,
    job_id: UUID,
    chain: Any,
) -> List[RelationObservation]:
    """
    Stage 4: re-read the documentation for one pair whose evidence is thin or one-sided.

    A narrow question against the right fragments recovers the inverse attribute and the
    cardinality that a broad question against an arbitrary fragment cannot.
    """
    result = await _invoke_pass(
        chain=chain,
        payload={
            "class_a": class_a,
            "class_b": class_b,
            "class_metadata": json.dumps(list(class_metadata), ensure_ascii=False, indent=1),
            "known_observations": _observations_json(known_observations),
            "documentation": documentation,
        },
        run_name="PairFocus",
        job_id=job_id,
        failure_context=f"Pair focus failed for {class_a}/{class_b}",
    )
    if not isinstance(result, RelationObservationsResponse):
        return []
    return list(result.observations or [])


async def adjudicate_pair(
    *,
    class_a: str,
    class_b: str,
    class_metadata: Sequence[Dict[str, Any]],
    known_attributes: Dict[str, List[str]],
    observed_attributes: Dict[str, List[str]],
    observations: Sequence[RelationObservation],
    job_id: UUID,
    chain: Any,
) -> Optional[RelationPairJudgement]:
    """
    Stage 5: decide what one class pair holds, with every observation for it in view.

    This is the only stage that accepts or rejects, the only one that assigns the
    subject/object orientation and the relation kind, and the only one positioned to see
    that different documented attributes on one pair may carry separate associations.
    """
    result = await _invoke_pass(
        chain=chain,
        payload={
            "class_a": class_a,
            "class_b": class_b,
            "class_metadata": json.dumps(list(class_metadata), ensure_ascii=False, indent=1),
            "known_attributes": json.dumps(known_attributes, ensure_ascii=False, indent=1),
            "observed_attributes": json.dumps(observed_attributes, ensure_ascii=False, indent=1),
            "observations": _observations_json(observations),
        },
        run_name="Adjudicate",
        job_id=job_id,
        failure_context=f"Adjudication failed for {class_a}/{class_b}",
    )
    return cast(Optional[RelationPairJudgement], result if isinstance(result, RelationPairJudgement) else None)


async def verify_relation(
    *,
    relation_json: str,
    class_metadata: Sequence[Dict[str, Any]],
    observations: Sequence[RelationObservation],
    known_attributes: Dict[str, List[str]],
    job_id: UUID,
    chain: Any,
) -> Optional[RelationRefutation]:
    """
    Stage 6: try to refute an accepted relation.

    Returning ``None`` means the verification pass itself failed; the caller keeps the
    relation rather than dropping it for an infrastructure reason.
    """
    result = await _invoke_pass(
        chain=chain,
        payload={
            "relation": relation_json,
            "class_metadata": json.dumps(list(class_metadata), ensure_ascii=False, indent=1),
            "observations": _observations_json(observations),
            "known_attributes": json.dumps(known_attributes, ensure_ascii=False, indent=1),
        },
        run_name="Verify",
        job_id=job_id,
        failure_context="Verification failed",
    )
    return cast(Optional[RelationRefutation], result if isinstance(result, RelationRefutation) else None)


def _observations_json(observations: Sequence[RelationObservation]) -> str:
    return json.dumps(
        [observation.model_dump(by_alias=True, exclude_none=True) for observation in observations],
        ensure_ascii=False,
        indent=1,
    )
