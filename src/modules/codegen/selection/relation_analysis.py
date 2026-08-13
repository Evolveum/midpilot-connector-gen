# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Selection of the relation-analysis detail that relation code generation needs.

``relationsOutput`` is the midPoint-facing contract: seven fields per relation, with no room
for how the association is actually carried. The relation pipeline persists the rest under
``relationsAnalysisOutput``, and for an association carried by a third class that rest is not
decoration but the executable part - such a relation has no attribute on either end, and its
carrying class is named nowhere in the record.

This module joins one emitted record back onto the verdict it was projected from and keeps
only what the generator can act on: the relation kind, the class carrying it, and the
attributes of that class pointing at each end. Pure selection - no I/O, no prompt text.
"""

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import ValidationError

from src.documents.normalize import normalize_object_class_name
from src.modules.codegen.schema import RelationCodegenContext, RelationLinkAttribute
from src.modules.digester.entities.relations import relation_identity, split_relation_tokens
from src.modules.digester.schemas import (
    RelationDecision,
    RelationPairAnalysis,
    RelationRecord,
    RelationsAnalysis,
    RelationVerdict,
)
from src.modules.digester.schemas.relation_analysis import (
    DETERMINISTIC_EVIDENCE_KINDS,
    NON_REFERENCE_EVIDENCE_KINDS,
)

logger = logging.getLogger(__name__)

LOG_SCOPE = "Codegen:Relation"

_LINK_OBJECT_KIND = "link_object"


def select_relation_codegen_context(
    analysis_payload: Any,
    relation: RelationRecord,
) -> Optional[RelationCodegenContext]:
    """
    Project the stored relation analysis onto the context for one emitted relation.

    Returns ``None`` when the analysis is missing, unreadable or does not contain the
    relation. Generation then proceeds on the record alone, exactly as before this context
    existed; every one of those cases is logged rather than passed on silently, because a
    ``link_object`` relation generated without it is quietly ungrounded.
    """
    if analysis_payload is None:
        logger.info(
            "[%s] No stored relation analysis for %s; generating from the record alone",
            LOG_SCOPE,
            relation.name,
        )
        return None

    try:
        analysis = RelationsAnalysis.model_validate(analysis_payload)
    except ValidationError:
        logger.warning(
            "[%s] Stored relation analysis is not readable; generating %s from the record alone",
            LOG_SCOPE,
            relation.name,
        )
        return None

    matched = _match_decision(analysis, relation)
    if matched is None:
        return None

    pair, decision = matched
    verdict = decision.verdict
    link_object_class = _resolve_link_object_class(pair, verdict, relation)
    link_attributes = (
        _link_attributes(analysis, link_object_class, (relation.subject, relation.object)) if link_object_class else []
    )

    if link_object_class and not link_attributes:
        logger.warning(
            "[%s] Relation %s is carried by %s but no observation names its attributes; "
            "the generator gets the class and its documentation only",
            LOG_SCOPE,
            relation.name,
            link_object_class,
        )

    return RelationCodegenContext(
        kind=verdict.kind,
        link_object_class=link_object_class,
        link_attributes=link_attributes,
    )


def relation_documentation_classes(
    relation: RelationRecord,
    context: Optional[RelationCodegenContext],
) -> List[str]:
    """
    Object classes whose documentation the relation generator has to see.

    The carrying class is one of them: its documentation holds the endpoints that implement
    the association, which appear in neither the subject's nor the object's documentation.
    """
    names = [relation.subject, relation.object]
    if context is not None and context.link_object_class:
        names.append(context.link_object_class)

    selected: List[str] = []
    seen: set[str] = set()
    for name in names:
        key = normalize_object_class_name(name)
        if not key or key in seen:
            continue
        seen.add(key)
        selected.append(name)
    return selected


def _match_decision(
    analysis: RelationsAnalysis,
    relation: RelationRecord,
) -> Optional[Tuple[RelationPairAnalysis, RelationDecision]]:
    """
    Find the accepted decision the emitted record was projected from.

    Joined on the relation identity rather than on ``name``: duplicate-name resolution can
    rename a record after projection, while the class pair and the attributes that
    distinguish one association of that pair from another survive it.
    """
    identity = relation_identity(
        relation.subject,
        relation.object,
        relation.subject_attribute,
        relation.object_attribute,
    )
    candidates = [
        (pair, decision)
        for pair in analysis.pairs
        for decision in pair.decisions
        if decision.accepted
        and relation_identity(
            decision.verdict.subject,
            decision.verdict.object,
            decision.verdict.subject_attribute,
            decision.verdict.object_attribute,
        )
        == identity
    ]

    if not candidates:
        logger.warning(
            "[%s] Relation %s has no accepted decision in the stored analysis; generating from the record alone",
            LOG_SCOPE,
            relation.name,
        )
        return None
    if len(candidates) == 1:
        return candidates[0]

    # Several associations of one pair share an identity only when neither side names an
    # attribute. Semantic deduplication keeps those apart by their relation name, which then
    # survives duplicate-name resolution untouched precisely because it is already distinct -
    # so here, and only here, the name is the reliable discriminator. Attaching the wrong
    # decision would describe one association with another one's carrier, so an unresolved
    # tie yields no context at all.
    named = [
        candidate
        for candidate in candidates
        if _canonical_relation_name(candidate[1].verdict.name) == _canonical_relation_name(relation.name)
    ]
    if len(named) == 1:
        return named[0]

    logger.warning(
        "[%s] Relation %s matches %d accepted decisions in the stored analysis; generating from the record alone",
        LOG_SCOPE,
        relation.name,
        len(candidates),
    )
    return None


def _canonical_relation_name(value: str) -> str:
    """Relation identifier in the form duplicate-name resolution produces."""
    return "_".join(split_relation_tokens(value))


def _resolve_link_object_class(
    pair: RelationPairAnalysis,
    verdict: RelationVerdict,
    relation: RelationRecord,
) -> str:
    """
    Name the class carrying the association, for a ``link_object`` verdict only.

    Adjudication is asked to name it, but the pair it judged was synthesized from an
    association class in the first place, and that class is recorded on the observation as
    ``viaClass``. Recovering it from there when the verdict left the field empty repairs the
    one case where the verdict knows less than the evidence it was built from. For any other
    kind the verdict decided the association is direct, and that decision stands.
    """
    if verdict.kind != _LINK_OBJECT_KIND:
        return ""

    ends = {normalize_object_class_name(relation.subject), normalize_object_class_name(relation.object)}
    named = verdict.link_object_class.strip()
    if named:
        # A carrier is by definition a third class. Naming one of the ends contradicts the
        # kind, and passing it on would tell the generator to resolve a class through itself.
        if normalize_object_class_name(named) in ends:
            logger.warning(
                "[%s] Relation %s names %s as its carrying class, which is one of its own ends; ignoring it",
                LOG_SCOPE,
                relation.name,
                named,
            )
            return ""
        return named

    for observation in pair.observations:
        via_class = observation.via_class.strip()
        if via_class and normalize_object_class_name(via_class) not in ends:
            logger.info(
                "[%s] Adjudication named no carrying class for %s; using %s from the observed evidence",
                LOG_SCOPE,
                relation.name,
                via_class,
            )
            return via_class

    logger.warning(
        "[%s] Relation %s is classified as link_object but no carrying class is known",
        LOG_SCOPE,
        relation.name,
    )
    return ""


def _link_attributes(
    analysis: RelationsAnalysis,
    link_object_class: str,
    ends: Sequence[str],
) -> List[RelationLinkAttribute]:
    """
    Attributes of the carrying class that point at each end of the association.

    Scanned across every pair of the analysis, not just the judged one: the evidence that
    ``Membership.user`` points at ``User`` was folded onto the ``Membership|User`` pair, which
    is precisely the pair the association itself is not on.

    Every unique attribute pointing from the carrying class to either relation end is kept.
    The selection is semantic rather than count-based: unrelated, embedded and inheritance
    attributes are excluded, while deterministic schema evidence is ordered before weaker
    evidence so the prompt retains both completeness and a useful preference signal.
    """
    link_key = normalize_object_class_name(link_object_class)
    end_names = {normalize_object_class_name(end): end for end in ends}

    per_end: Dict[str, List[Tuple[bool, str]]] = {key: [] for key in end_names}
    seen: Dict[str, set[str]] = {key: set() for key in end_names}
    for pair in analysis.pairs:
        for observation in pair.observations:
            if observation.evidence_kind in NON_REFERENCE_EVIDENCE_KINDS:
                continue
            if normalize_object_class_name(observation.source_class) != link_key:
                continue
            end_key = normalize_object_class_name(observation.target_class)
            if end_key not in per_end:
                continue
            attribute = observation.source_attribute.strip()
            canonical = "".join(split_relation_tokens(attribute))
            if not canonical or canonical in seen[end_key]:
                continue
            seen[end_key].add(canonical)
            per_end[end_key].append((observation.evidence_kind in DETERMINISTIC_EVIDENCE_KINDS, attribute))

    selected: List[RelationLinkAttribute] = []
    for end_key, end_name in end_names.items():
        ranked = sorted(per_end[end_key], key=lambda item: not item[0])
        selected.extend(RelationLinkAttribute(attribute=attribute, references=end_name) for _, attribute in ranked)
    return selected
