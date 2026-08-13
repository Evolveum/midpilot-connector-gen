# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Staged relation detection for REST documentation.

A relation is a cross-chunk object: chunking splits the subject schema, the object schema
and the sub-resource endpoint into different fragments, so no single fragment holds enough
to judge one. The pipeline therefore separates observing from deciding.

    1 harvest      one call per chunk, recall-first, no rejection
    2 seed         attributes, endpoints and class metadata already in the session
    3 sweep        one call per object class, with that class's whole documentation in view
    4 focus        one call per pair whose evidence is thin or one-sided
    5 adjudicate   one call per class pair, with every observation for it at once
    6 verify       one adversarial call per accepted relation
    7 project      down to the unchanged ``RelationsResponse`` contract

Stages 1-4 only produce observations, which are folded onto an **unordered class pair**;
that is what makes two documented navigation directions one relation rather than two
half-populated records. Stage 5 is the only stage that accepts, rejects, assigns the
subject/object orientation, and decides how many associations the pair holds.

One pair can hold several: a class that records both the instances belonging to it and the
instances responsible for it is connected to the same partner class twice. Each association
is its own :class:`RelationDecision`, verified and projected separately, and the stages that
could quietly collapse them back into one - attribute grounding, verification corrections and
semantic deduplication - are each constrained so they cannot.

Everything the pipeline learns beyond the seven contract fields - relation kind, per-side
cardinality, link class, confidence, rationale and the explicit rejections - is persisted
under ``relationsAnalysisOutput`` instead of being thrown away.
"""

import asyncio
import json
import logging
from enum import IntEnum
from typing import Any, Awaitable, Dict, List, Mapping, Optional, Sequence, Tuple, TypeVar
from uuid import UUID

from src.config import config
from src.documents.normalize import normalize_object_class_name
from src.jobs import append_job_error, increment_processed_documents, update_job_progress
from src.modules.digester.entities.relation_candidates import (
    ObjectClassIndex,
    ObjectClassInfo,
    ObservedPair,
    deduplicate_relation_names,
    disambiguate_relation_labels,
    expand_link_object_pairs,
    grounded_attribute_names,
    group_observations,
    is_attribute_grounded,
    observations_from_attributes,
    observations_from_class_metadata,
    observations_from_endpoints,
    sort_relations_by_iga_priority,
    verdict_to_relation_record,
)
from src.modules.digester.entities.relations import (
    deduplicate_semantic_relations,
    relation_identity,
    split_relation_tokens,
)
from src.modules.digester.extraction.llm_execution import run_chunks_concurrently
from src.modules.digester.extraction.metadata_helper import build_doc_metadata_map
from src.modules.digester.extractors.rest import relation_context, relation_passes
from src.modules.digester.extractors.rest.relation_context import LOG_SCOPE
from src.modules.digester.results import store_relations_analysis
from src.modules.digester.schemas import RelationRecord, RelationsResponse
from src.modules.digester.schemas.relation_analysis import (
    RELATION_KINDS_ACCEPTED,
    EvidenceSource,
    RelationAnalysisStats,
    RelationDecision,
    RelationObservation,
    RelationPairAnalysis,
    RelationsAnalysis,
    RelationVerdict,
)
from src.shared.enums import JobStage

logger = logging.getLogger(__name__)

# One observation plus where it came from and which chunk backs it.
ObservationEntry = Tuple[RelationObservation, EvidenceSource, Optional[Sequence[Dict[str, str]]]]

T = TypeVar("T")


# --- Progress reporting ---


class RelationStep(IntEnum):
    """Ordered user-visible steps of the relation pipeline.

    Six stages run over four different units of work - chunks, object classes, candidate
    pairs and accepted relations - so a bare completed/total pair cannot say what is being
    counted. The step number and its unit go into the progress message; the counters carry
    the position within the running step.

    The numbering is fixed rather than derived from what actually runs. A step can be
    skipped (no chunks selected, sweep disabled, no weak pairs, verification turned off),
    and a gap in the sequence is more honest than renumbering the remaining steps mid-run.
    """

    seed = 1
    harvest = 2
    sweep = 3
    focus = 4
    judge = 5
    verify = 6


_STEP_LABELS: Dict[RelationStep, str] = {
    RelationStep.seed: "Reading extracted schema",
    RelationStep.harvest: "Harvesting evidence from documentation",
    RelationStep.sweep: "Sweeping object classes",
    RelationStep.focus: "Re-reading uncertain candidates",
    RelationStep.judge: "Judging relation candidates",
    RelationStep.verify: "Verifying relations",
}


def _step_message(step: RelationStep, detail: str) -> str:
    """Progress message naming the step, its position in the pipeline and its unit of work."""
    return f"Step {int(step)}/{len(RelationStep)} - {_STEP_LABELS[step]}: {detail}"


async def _start_step(
    job_id: UUID,
    step: RelationStep,
    *,
    stage: JobStage,
    total: int,
    detail: str,
    completed: int = 0,
) -> None:
    """Announce a step and reset the counters to its own unit of work.

    ``processing_completed`` is written explicitly on every step: progress updates are
    partial, so a step that only set a new total would leave the previous step's completed
    count in place and report progress it has not made.
    """
    await update_job_progress(
        job_id,
        stage=stage,
        total_processing=total,
        processing_completed=completed,
        message=_step_message(step, detail),
    )


async def _counted(work: Awaitable[T], job_id: UUID) -> T:
    """Run one unit of a step and count it, whichever way the unit ends.

    Wrapping the whole unit rather than incrementing before each ``return`` is what keeps the
    counter honest: several of these stages bail out early for units with no documentation,
    and an uncounted early exit leaves the step permanently short of its total.
    """
    try:
        return await work
    finally:
        await increment_processed_documents(job_id, delta=1)


# --- Entry point ---


async def extract_relations(
    doc_items: List[dict],
    relevant_object_classes: Any,
    class_schema_snapshot: Mapping[str, Any],
    session_id: UUID,
    job_id: UUID,
) -> Dict[str, Any]:
    """
    Detect relations between the session's object classes.

    Args:
        doc_items: Documentation chunks selected for this session.
        relevant_object_classes: Stored ``objectClassesOutput`` payload.
        class_schema_snapshot: Attribute and endpoint outputs captured when the job was scheduled.
        session_id: Session receiving relation analysis state.
        job_id: Job used for progress, errors and the stale-write guard on the analysis.
    """
    index, skipped_classes = ObjectClassIndex.from_payload(relevant_object_classes)
    if skipped_classes:
        logger.warning("[%s] Skipped %d malformed object-class entries while indexing", LOG_SCOPE, skipped_classes)
    if not len(index):
        detail = "No usable object classes in objectClassesOutput; nothing to relate"
        logger.error("[%s] %s", LOG_SCOPE, detail)
        await append_job_error(job_id, f"[{LOG_SCOPE}] {detail}")
        return _empty_result()

    chunk_lookup = relation_context.build_chunk_lookup(doc_items)
    prompt_classes = relation_context.classes_for_prompt(index)
    object_classes_json = json.dumps(index.to_prompt_payload(prompt_classes), ensure_ascii=False, indent=1)

    attributes_by_class, endpoints_by_class = relation_context.unpack_relation_schema_snapshot(
        class_schema_snapshot,
        index,
    )
    class_chunk_ids = await relation_context.load_class_chunk_ids(session_id, index, chunk_lookup)

    entries: List[ObservationEntry] = []
    stats = RelationAnalysisStats()

    entries.extend(_seed_from_session(index, attributes_by_class, endpoints_by_class, chunk_lookup, stats))
    # Seeding is deterministic and needs no I/O, so it is reported as already complete; the
    # step still gets its own line so the counts it worked from are visible in the CLI.
    await _start_step(
        job_id,
        RelationStep.seed,
        stage=JobStage.processing,
        total=len(index),
        completed=len(index),
        detail=(
            f"{len(index)} object classes, {len(attributes_by_class)} with extracted attributes, "
            f"{len(endpoints_by_class)} with extracted endpoints"
        ),
    )
    entries.extend(await _harvest(doc_items, object_classes_json, job_id, stats))
    entries.extend(await _sweep_classes(index, object_classes_json, class_chunk_ids, chunk_lookup, job_id, stats))

    # An association class connects two ends that no stage ever pairs directly; its evidence has
    # to be reshaped before grouping, or the pair the domain needs is never formed.
    link_entries, link_summary = expand_link_object_pairs(entries, index, attributes_by_class)
    if link_entries:
        stats.link_object_pairs_expanded = len(link_entries)
        logger.info(
            "[%s] Expanded %d association-class pair(s): %s",
            LOG_SCOPE,
            len(link_entries),
            "; ".join(link_summary[:10]),
        )
        entries.extend(link_entries)

    # Grouped twice on purpose: the focused re-read needs to know which pairs are weak, and its
    # own observations then have to land on those same pairs.
    pairs, _ = group_observations(entries, index)
    entries.extend(await _focus_weak_pairs(pairs, index, class_chunk_ids, chunk_lookup, job_id, stats))
    pairs, unresolved = group_observations(entries, index)

    stats.observations_total = len(entries)
    if unresolved:
        logger.info("[%s] Dropped %d observations naming classes that were never extracted", LOG_SCOPE, unresolved)

    analyses = await _adjudicate_pairs(pairs, index, attributes_by_class, job_id, stats)
    relations = await _verify_and_collect(analyses, index, attributes_by_class, job_id, stats)

    await _persist_analysis(session_id, job_id, analyses, stats)

    relevant_documentations = _relevant_documentations(analyses)
    logger.info(
        "[%s] Completed: %d pairs analyzed, %d relations emitted, %d observations from %d chunks",
        LOG_SCOPE,
        len(analyses),
        len(relations),
        stats.observations_total,
        stats.chunks_harvested,
    )

    return {
        "result": RelationsResponse(relations=relations).model_dump(by_alias=True),
        "relevantDocumentations": relevant_documentations,
    }


def _empty_result() -> Dict[str, Any]:
    return {"result": RelationsResponse(relations=[]).model_dump(by_alias=True), "relevantDocumentations": []}


# --- Stage 2: deterministic seeding ---


def _seed_from_session(
    index: ObjectClassIndex,
    attributes_by_class: Dict[str, Any],
    endpoints_by_class: Dict[str, Any],
    chunk_lookup: Dict[str, Dict[str, Any]],
    stats: RelationAnalysisStats,
) -> List[ObservationEntry]:
    """
    Turn already-extracted digester output into observations, without an LLM call.

    Attribute schemas carry the distinction relation detection needs: ``format=reference``
    means the attribute points at another object class, ``format=embedded`` means it belongs
    to this one. Both are recorded so the embedded case is rejected explicitly instead of
    being rediscovered and re-proposed every run.
    """
    entries: List[ObservationEntry] = []

    for info in index.all:
        key = normalize_object_class_name(info.name)
        payload = attributes_by_class.get(key)
        if payload is not None:
            for observation in observations_from_attributes(info.name, payload, index):
                entries.append((observation, "attribute_schema", None))
        endpoints = endpoints_by_class.get(key)
        if endpoints is not None:
            for observation in observations_from_endpoints(info.name, endpoints, index):
                entries.append((observation, "endpoint_schema", None))

    for observation in observations_from_class_metadata(index):
        entries.append((observation, "object_class_metadata", None))

    stats.observations_deterministic = len(entries)
    logger.info("[%s] Seeded %d observations from stored schema output", LOG_SCOPE, len(entries))
    return entries


# --- Stage 1: per-chunk harvest ---


async def _harvest(
    doc_items: List[dict],
    object_classes_json: str,
    job_id: UUID,
    stats: RelationAnalysisStats,
) -> List[ObservationEntry]:
    """Run the recall-first harvest over every selected chunk."""
    if not doc_items:
        return []

    await _start_step(
        job_id,
        RelationStep.harvest,
        stage=JobStage.processing_chunks,
        total=len(doc_items),
        detail=f"{len(doc_items)} documentation chunks",
    )

    chain = relation_passes.build_harvest_chain()
    metadata_map = build_doc_metadata_map(doc_items)
    chunk_id_to_doc_id = {
        str(item["chunkId"]): str(item["docId"]) for item in doc_items if item.get("chunkId") and item.get("docId")
    }

    async def extractor(content: str, jid: UUID, chunk_id: UUID) -> Tuple[List[RelationObservation], bool]:
        return await relation_passes.harvest_chunk(
            content=content,
            job_id=jid,
            chunk_id=chunk_id,
            chunk_metadata=metadata_map.get(str(chunk_id)),
            object_classes_json=object_classes_json,
            chain=chain,
        )

    # set_total=False keeps this step's message and counter reset: the shared helper would
    # otherwise overwrite them with its own generic "Processing chunks" line. It still
    # increments the completed count per chunk.
    results = await run_chunks_concurrently(
        chunk_items=doc_items,
        job_id=job_id,
        extractor=extractor,
        set_total=False,
    )

    entries: List[ObservationEntry] = []
    for observations, _has_relevant_data, chunk_id in results:
        doc_id = chunk_id_to_doc_id.get(str(chunk_id))
        refs = [{"doc_id": doc_id, "chunk_id": str(chunk_id)}] if doc_id else None
        for observation in observations or []:
            entries.append((observation, "chunk_harvest", refs))

    stats.chunks_harvested = len(doc_items)
    logger.info("[%s] Harvested %d observations from %d chunks", LOG_SCOPE, len(entries), len(doc_items))
    return entries


# --- Stage 3: per-class sweep ---


async def _sweep_classes(
    index: ObjectClassIndex,
    object_classes_json: str,
    class_chunk_ids: Dict[str, List[str]],
    chunk_lookup: Dict[str, Dict[str, Any]],
    job_id: UUID,
    stats: RelationAnalysisStats,
) -> List[ObservationEntry]:
    """
    Ask, per object class, what it relates to - with that class's whole documentation in view.

    This is the pass that recovers links whose two ends live in different chunks, because the
    question is asked once per class instead of once per fragment.
    """
    limit = config.digester.relation_class_sweep_limit
    if limit <= 0:
        return []

    max_rank = 1 if config.digester.relation_sweep_include_medium_confidence else 0
    candidates = index.candidates(max_confidence_rank=max_rank, limit=limit)
    swept = [info for info in candidates if class_chunk_ids.get(normalize_object_class_name(info.name))]

    undocumented = [info.name for info in candidates if info not in swept]
    if undocumented:
        logger.warning(
            "[%s] %d eligible class(es) have no documentation mapped and get no sweep: %s",
            LOG_SCOPE,
            len(undocumented),
            ", ".join(undocumented[:10]),
        )
    if not swept:
        return []

    eligible = len(index.candidates(max_confidence_rank=max_rank, limit=0))
    if eligible > len(candidates):
        logger.warning(
            "[%s] Sweeping %d of %d eligible classes (relation_class_sweep_limit=%d)",
            LOG_SCOPE,
            len(candidates),
            eligible,
            limit,
        )

    await _start_step(
        job_id,
        RelationStep.sweep,
        stage=JobStage.processing_chunks,
        total=len(swept),
        detail=f"{len(swept)} of {eligible} eligible object classes",
    )
    chain = relation_passes.build_class_sweep_chain()

    async def sweep(info: ObjectClassInfo) -> List[ObservationEntry]:
        chunk_ids = class_chunk_ids[normalize_object_class_name(info.name)]
        documentation, skipped = relation_context.assemble_documentation(chunk_ids, chunk_lookup)
        if skipped:
            logger.info(
                "[%s] Class sweep for %s skipped %d chunk(s) over the context budget",
                LOG_SCOPE,
                info.name,
                skipped,
            )
        observations = await relation_passes.sweep_class(
            focus_class=info.name,
            focus_description=info.description,
            object_classes_json=object_classes_json,
            documentation=documentation,
            job_id=job_id,
            chain=chain,
        )
        refs = relation_context.chunk_refs(chunk_ids, chunk_lookup)
        return [(observation, "class_sweep", refs) for observation in observations]

    swept_results = await asyncio.gather(*(_counted(sweep(info), job_id) for info in swept))
    entries = [entry for group in swept_results for entry in group]
    stats.classes_swept = len(swept)
    logger.info("[%s] Class sweep produced %d observations over %d classes", LOG_SCOPE, len(entries), len(swept))
    return entries


# --- Stage 4: focused re-read of weak pairs ---


async def _focus_weak_pairs(
    pairs: Dict[str, ObservedPair],
    index: ObjectClassIndex,
    class_chunk_ids: Dict[str, List[str]],
    chunk_lookup: Dict[str, Dict[str, Any]],
    job_id: UUID,
    stats: RelationAnalysisStats,
) -> List[ObservationEntry]:
    """Re-read the documentation for pairs whose evidence is thin or one-sided."""
    weak = [pair for pair in pairs.values() if pair.is_weak()]
    if not weak:
        return []

    await _start_step(
        job_id,
        RelationStep.focus,
        stage=JobStage.processing_chunks,
        total=len(weak),
        detail=f"{len(weak)} of {len(pairs)} candidate pairs have thin or one-sided evidence",
    )
    chain = relation_passes.build_pair_focus_chain()

    async def focus(pair: ObservedPair) -> Tuple[List[ObservationEntry], bool]:
        chunk_ids = relation_context.pair_chunk_ids(pair, class_chunk_ids)
        if not chunk_ids:
            logger.info("[%s] No documentation maps to pair %s; skipping its re-read", LOG_SCOPE, pair.key)
            return [], False
        documentation, skipped = relation_context.assemble_documentation(chunk_ids, chunk_lookup)
        if skipped:
            logger.info(
                "[%s] Re-read of %s skipped %d chunk(s) over the context budget",
                LOG_SCOPE,
                pair.key,
                skipped,
            )
        if not documentation.strip():
            return [], False
        observations = await relation_passes.focus_pair(
            class_a=pair.class_a,
            class_b=pair.class_b,
            class_metadata=index.to_prompt_payload(
                [info for info in (index.resolve(pair.class_a), index.resolve(pair.class_b)) if info]
            ),
            known_observations=pair.observations,
            documentation=documentation,
            job_id=job_id,
            chain=chain,
        )
        refs = relation_context.chunk_refs(chunk_ids, chunk_lookup)
        return [(observation, "pair_focus", refs) for observation in observations], True

    focused = await asyncio.gather(*(_counted(focus(pair), job_id) for pair in weak))
    entries = [entry for group, _called in focused for entry in group]
    stats.pairs_refocused = sum(1 for _group, called in focused if called)
    logger.info(
        "[%s] Focused re-read produced %d observations over %d of %d selected pairs",
        LOG_SCOPE,
        len(entries),
        stats.pairs_refocused,
        len(weak),
    )
    return entries


# --- Stage 5: adjudication ---


async def _adjudicate_pairs(
    pairs: Dict[str, ObservedPair],
    index: ObjectClassIndex,
    attributes_by_class: Dict[str, Any],
    job_id: UUID,
    stats: RelationAnalysisStats,
) -> List[RelationPairAnalysis]:
    """Decide every pair, with all of its observations in view."""
    if not pairs:
        return []

    # Ordered by evidence strength so the safety ceiling, if it bites, drops the weakest
    # candidates rather than an arbitrary alphabetical tail.
    ranked = sorted(pairs.values(), key=lambda pair: (tuple(-value for value in pair.evidence_strength()), pair.key))
    limit = config.digester.relation_max_adjudicated_pairs
    ordered = ranked[:limit]
    if len(ranked) > limit:
        dropped = [pair.key for pair in ranked[limit:]]
        logger.warning(
            "[%s] Judging %d of %d pairs (relation_max_adjudicated_pairs=%d); dropped: %s",
            LOG_SCOPE,
            len(ordered),
            len(ranked),
            limit,
            ", ".join(dropped[:20]),
        )
        await append_job_error(
            job_id,
            f"[{LOG_SCOPE}] {len(dropped)} relation candidate pair(s) were not judged because the "
            f"relation_max_adjudicated_pairs limit of {limit} was reached",
        )

    detail = f"{len(ordered)} candidate pairs"
    if len(ranked) > limit:
        detail = f"{len(ordered)} of {len(ranked)} candidate pairs (capped by relation_max_adjudicated_pairs)"
    await _start_step(
        job_id,
        RelationStep.judge,
        stage=JobStage.building,
        total=len(ordered),
        detail=detail,
    )
    chain = relation_passes.build_adjudication_chain()

    def pair_class_metadata(pair: ObservedPair) -> List[Dict[str, Any]]:
        """Class descriptions for the judged pair, listed once even when both sides are one class."""
        resolved = [info for info in (index.resolve(pair.class_a), index.resolve(pair.class_b)) if info]
        unique = {normalize_object_class_name(info.name): info for info in resolved}
        return index.to_prompt_payload(list(unique.values()))

    async def judge(pair: ObservedPair) -> RelationPairAnalysis:
        judgement = await relation_passes.adjudicate_pair(
            class_a=pair.class_a,
            class_b=pair.class_b,
            class_metadata=pair_class_metadata(pair),
            known_attributes=relation_context.known_attributes_for((pair.class_a, pair.class_b), attributes_by_class),
            observed_attributes=_observed_attributes(pair),
            observations=pair.observations,
            job_id=job_id,
            chain=chain,
        )
        analysis = RelationPairAnalysis(
            pair_key=pair.key,
            class_a=pair.class_a,
            class_b=pair.class_b,
            observation_sources=list(pair.sources),
            observations=list(pair.observations),
            relevant_documentations=list(pair.chunk_refs),
        )
        if judgement is None:
            analysis.rejection_reason = "Adjudication produced no judgement"
            return analysis

        for verdict in judgement.relations:
            normalized = _normalize_verdict(verdict, pair)
            if normalized is None:
                analysis.decisions.append(
                    RelationDecision(
                        verdict=verdict,
                        rejection_reason=(
                            "Adjudication named a subject/object outside the class pair or reused one side "
                            "for a non-self relation"
                        ),
                    )
                )
                continue
            decision = RelationDecision(verdict=normalized)
            if not normalized.is_relation or normalized.kind not in RELATION_KINDS_ACCEPTED:
                decision.rejection_reason = normalized.rationale.strip() or f"Classified as {normalized.kind}"
            analysis.decisions.append(decision)

        if not analysis.decisions:
            analysis.rejection_reason = judgement.rationale.strip() or f"Classified as {judgement.rejection_kind}"
        return analysis

    analyses = list(await asyncio.gather(*(_counted(judge(pair), job_id) for pair in ordered)))
    stats.pairs_adjudicated = len(analyses)
    candidates = sum(len(analysis.decisions) for analysis in analyses)
    logger.info("[%s] Adjudicated %d pairs into %d candidate association(s)", LOG_SCOPE, len(analyses), candidates)
    return analyses


def _observed_attributes(pair: ObservedPair) -> Dict[str, List[str]]:
    """Distinct attributes the evidence attached to each side of the pair.

    Handed to adjudication because an uneven split is the signature of a pair carrying more
    than one association: a class listing both its members and its owners against a partner
    that names only one attribute has a second association still missing its other end.

    A self-pair has one class on both sides, so its two buckets are merged: keyed by class
    name they would otherwise collapse onto a single entry and the surviving side would hide
    every attribute observed on the other.
    """
    side_a, side_b = pair.attributes_per_side()
    if pair.is_self_pair:
        return {pair.class_a: list(dict.fromkeys(side_a + side_b))}
    return {pair.class_a: side_a, pair.class_b: side_b}


def _normalize_verdict(
    verdict: RelationVerdict,
    pair: ObservedPair,
) -> Optional[RelationVerdict]:
    """
    Canonicalize a verdict only when both of its classes belong to the judged pair.

    Invalid orientation is rejected rather than repaired with a name-based guess. Only the
    adjudication LLM, which sees class descriptions and evidence, may assign semantics.
    """
    if not verdict.is_relation:
        return verdict

    members = {
        normalize_object_class_name(pair.class_a): pair.class_a,
        normalize_object_class_name(pair.class_b): pair.class_b,
    }
    resolved_subject = members.get(normalize_object_class_name(verdict.subject))
    resolved_object = members.get(normalize_object_class_name(verdict.object))
    if resolved_subject is None or resolved_object is None:
        return None
    if not pair.is_self_pair and resolved_subject == resolved_object:
        return None
    return verdict.model_copy(update={"subject": resolved_subject, "object": resolved_object})


# --- Stage 6: verification and projection ---


async def _verify_and_collect(
    analyses: List[RelationPairAnalysis],
    index: ObjectClassIndex,
    attributes_by_class: Dict[str, Any],
    job_id: UUID,
    stats: RelationAnalysisStats,
) -> List[RelationRecord]:
    """Refute what survived adjudication, then project the survivors onto the API contract."""
    pending = [
        (analysis, decision)
        for analysis in analyses
        for decision in analysis.decisions
        if not decision.rejection_reason
    ]
    if not pending:
        return []

    if config.digester.relation_verification_enabled:
        await _start_step(
            job_id,
            RelationStep.verify,
            stage=JobStage.building,
            total=len(pending),
            detail=f"{len(pending)} associations accepted by adjudication",
        )
        chain = relation_passes.build_verification_chain()
        await asyncio.gather(
            *(
                _counted(_verify_one(analysis, decision, index, attributes_by_class, job_id, chain), job_id)
                for analysis, decision in pending
            )
        )
        stats.relations_verified = len(pending)
        for analysis in {id(item[0]): item[0] for item in pending}.values():
            _apply_verification_corrections(analysis)

    projected: List[Tuple[RelationPairAnalysis, RelationDecision, RelationRecord]] = []
    for analysis, decision in pending:
        if decision.rejection_reason:
            continue
        _ground_decision_attributes(analysis, decision, attributes_by_class)
        record = verdict_to_relation_record(decision.verdict)
        if record is None:
            decision.rejection_reason = "Verdict could not be projected onto a relation record"
            continue
        projected.append((analysis, decision, record))

    relations = deduplicate_semantic_relations([record for _analysis, _decision, record in projected])
    _record_merged_away_decisions(projected, relations)

    relations = disambiguate_relation_labels(deduplicate_relation_names(relations))
    relations = sort_relations_by_iga_priority(relations, index)
    stats.relations_emitted = len(relations)
    return relations


def _record_merged_away_decisions(
    projected: Sequence[Tuple[RelationPairAnalysis, RelationDecision, RelationRecord]],
    surviving: Sequence[RelationRecord],
) -> None:
    """Mark decisions whose record was merged away, so the analysis matches the response.

    Semantic deduplication is a safety net against wording-only duplicates, but it works on
    records and cannot report which decision it dropped. Without this reconciliation the
    persisted analysis claims two accepted associations while midPoint receives one, and the
    loss appears nowhere - not in the response, the analysis, the job errors or the log.
    """
    remaining: Dict[Tuple[str, str, str, str], int] = {}
    for record in surviving:
        remaining[_record_identity(record)] = remaining.get(_record_identity(record), 0) + 1

    for analysis, decision, record in projected:
        identity = _record_identity(record)
        if remaining.get(identity, 0) > 0:
            remaining[identity] -= 1
            decision.accepted = True
            analysis.accepted = True
            continue
        decision.accepted = False
        decision.rejection_reason = "Merged into another association of the same pair as a duplicate"
        logger.warning(
            "[%s] Association %s on %s was merged into a duplicate and is not in the response",
            LOG_SCOPE,
            decision.verdict.name or "<unnamed>",
            analysis.pair_key,
        )


def _record_identity(record: RelationRecord) -> Tuple[str, str, str, str]:
    """What semantic deduplication treats as the same relation."""
    return relation_identity(record.subject, record.object, record.subject_attribute, record.object_attribute)


async def _verify_one(
    analysis: RelationPairAnalysis,
    decision: RelationDecision,
    index: ObjectClassIndex,
    attributes_by_class: Dict[str, Any],
    job_id: UUID,
    chain: Any,
) -> None:
    """Ask a skeptic to refute one relation; a failed verification keeps the relation."""
    refutation = await relation_passes.verify_relation(
        relation_json=decision.verdict.model_dump_json(by_alias=True, exclude_none=True),
        class_metadata=index.to_prompt_payload(
            [info for info in (index.resolve(analysis.class_a), index.resolve(analysis.class_b)) if info]
        ),
        observations=analysis.observations,
        known_attributes=relation_context.known_attributes_for(
            (analysis.class_a, analysis.class_b), attributes_by_class
        ),
        job_id=job_id,
        chain=chain,
    )
    decision.refutation = refutation
    if refutation is None:
        return

    if refutation.refuted:
        decision.rejection_reason = refutation.reason.strip() or "Refuted during verification"


def _apply_verification_corrections(analysis: RelationPairAnalysis) -> None:
    """
    Apply the attribute corrections verification asked for, unless they erase an association.

    A skeptic judges one association but is shown the whole pair's evidence, so it can propose
    the attributes of a sibling association - membership's ``groups``/``members`` while judging
    ownership. Applying that would make the two records identical and the later semantic dedup
    would silently drop one, turning two real associations into one. A correction that collides
    with a sibling is therefore refused and recorded rather than applied.
    """
    live = [decision for decision in analysis.decisions if not decision.rejection_reason]

    def attribute_pair(verdict: RelationVerdict) -> Tuple[str, str]:
        return (
            "".join(split_relation_tokens(verdict.subject_attribute)),
            "".join(split_relation_tokens(verdict.object_attribute)),
        )

    for decision in live:
        refutation = decision.refutation
        if refutation is None:
            continue

        updates: Dict[str, Any] = {}
        if refutation.corrected_subject_attribute.strip():
            updates["subject_attribute"] = refutation.corrected_subject_attribute.strip()
        if refutation.corrected_object_attribute.strip():
            updates["object_attribute"] = refutation.corrected_object_attribute.strip()
        if not updates:
            continue

        corrected = decision.verdict.model_copy(update=updates)
        siblings = {attribute_pair(other.verdict) for other in live if other is not decision}
        if attribute_pair(corrected) in siblings:
            logger.info(
                "[%s] Refused a verification correction on %s that would duplicate another "
                "association of the same pair",
                LOG_SCOPE,
                analysis.pair_key,
            )
            continue
        decision.verdict = corrected


def _ground_decision_attributes(
    analysis: RelationPairAnalysis,
    decision: RelationDecision,
    attributes_by_class: Dict[str, Any],
) -> None:
    """
    Drop attribute names that appear neither in the class schema nor in the cited evidence.

    A fabricated attribute name reaches codegen as a real one, so an empty side is strictly
    better than an invented one. The relation itself survives - only the name is cleared.
    """
    verdict = decision.verdict

    # Per class, not pooled across the pair: `members` is evidence for Group, and accepting it
    # as a User attribute just because it was observed somewhere on this pair would let the
    # two sides of an association borrow each other's names.
    observed_by_class: Dict[str, set[str]] = {}
    for observation in analysis.observations:
        for class_name, attribute in (
            (observation.source_class, observation.source_attribute),
            (observation.target_class, observation.target_attribute),
        ):
            if not attribute.strip():
                continue
            bucket = observed_by_class.setdefault(normalize_object_class_name(class_name), set())
            bucket.add("".join(split_relation_tokens(attribute)))

    updates: Dict[str, Any] = {}
    for field_name, class_name, attribute in (
        ("subject_attribute", verdict.subject, verdict.subject_attribute),
        ("object_attribute", verdict.object, verdict.object_attribute),
    ):
        if not attribute.strip():
            continue
        known = grounded_attribute_names(attributes_by_class.get(normalize_object_class_name(class_name)))
        if is_attribute_grounded(attribute, known):
            continue
        observed = observed_by_class.get(normalize_object_class_name(class_name), set())
        if "".join(split_relation_tokens(attribute)) in observed:
            continue
        decision.ungrounded_attributes.append(f"{class_name}.{attribute}")
        updates[field_name] = ""

    if updates:
        logger.info(
            "[%s] Cleared ungrounded attribute name(s) on %s: %s",
            LOG_SCOPE,
            analysis.pair_key,
            ", ".join(decision.ungrounded_attributes),
        )
        decision.verdict = verdict.model_copy(update=updates)


# --- Persistence and evidence ---


async def _persist_analysis(
    session_id: UUID,
    job_id: UUID,
    analyses: List[RelationPairAnalysis],
    stats: RelationAnalysisStats,
) -> None:
    """Store the working state; a failure here must not fail the extraction."""
    observation_limit = config.digester.relation_max_stored_observations_per_pair
    stored_pairs: List[RelationPairAnalysis] = []
    truncated_observations = 0
    for pair in analyses:
        if len(pair.observations) <= observation_limit:
            stored_pairs.append(pair)
            continue
        truncated_observations += len(pair.observations) - observation_limit
        stored_pairs.append(pair.model_copy(update={"observations": pair.observations[:observation_limit]}))

    if truncated_observations:
        logger.info(
            "[%s] Omitted %d observations from persisted analysis due to the per-pair storage limit",
            LOG_SCOPE,
            truncated_observations,
        )

    analysis = RelationsAnalysis(job_id=str(job_id), stats=stats, pairs=stored_pairs)
    try:
        stored = await store_relations_analysis(
            session_id,
            job_id,
            analysis.model_dump(by_alias=True, mode="json"),
        )
    except Exception:
        logger.exception("[%s] Failed to persist relation analysis", LOG_SCOPE)
        return
    if not stored:
        logger.info("[%s] Relation analysis not stored; a newer relations job owns the session", LOG_SCOPE)


def _relevant_documentations(analyses: List[RelationPairAnalysis]) -> List[Dict[str, str]]:
    """Chunks backing accepted relations, falling back to every chunk that produced evidence."""
    accepted: List[Dict[str, str]] = []
    observed: List[Dict[str, str]] = []
    for analysis in analyses:
        for ref in analysis.relevant_documentations:
            doc_id = ref.get("doc_id") or ref.get("docId") or ""
            chunk_id = ref.get("chunk_id") or ref.get("chunkId") or ""
            if not doc_id or not chunk_id:
                continue
            normalized = {"doc_id": doc_id, "chunk_id": chunk_id}
            if normalized not in observed:
                observed.append(normalized)
            if analysis.accepted and normalized not in accepted:
                accepted.append(normalized)
    return accepted or observed
