# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Pure domain logic for staged relation detection.

Two jobs live here, both free of I/O and LLM calls so they can be tested directly:

* turning already-extracted digester output (object classes, attribute schemas,
  endpoint surfaces) into relation observations, so the LLM stages start from what the
  pipeline already knows instead of rediscovering it from prose, and
* folding observations from every stage onto one **unordered class pair**, which is what
  makes two documented navigation directions one association instead of two half-records.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.config import config
from src.documents.normalize import (
    canonical_object_class_key,
    dereference_attribute_type,
    normalize_object_class_name,
)
from src.modules.digester.entities.relations import split_relation_tokens
from src.modules.digester.enums import ConfidenceLevel
from src.modules.digester.schemas import RelationRecord
from src.modules.digester.schemas.relation_analysis import (
    DETERMINISTIC_EVIDENCE_KINDS,
    NON_REFERENCE_EVIDENCE_KINDS,
    EvidenceSource,
    RelationObservation,
    RelationVerdict,
)
from src.shared.coerce import is_true

logger = logging.getLogger(__name__)

_PATH_PARAMETER_RE = re.compile(r"^[{:<].*[}>]?$")

CONFIDENCE_RANK: Dict[ConfidenceLevel, int] = {
    ConfidenceLevel.HIGH: 0,
    ConfidenceLevel.MEDIUM: 1,
    ConfidenceLevel.LOW: 2,
}


# --- Object class index ---


@dataclass(frozen=True)
class ObjectClassInfo:
    """One extracted object class, reduced to what relation detection needs."""

    name: str
    description: str
    confidence: ConfidenceLevel
    embedded: bool
    abstract: bool
    superclass: str
    order: int

    @property
    def confidence_rank(self) -> int:
        return CONFIDENCE_RANK.get(self.confidence, len(CONFIDENCE_RANK))


def _coerce_confidence(value: Any) -> ConfidenceLevel:
    try:
        return ConfidenceLevel(str(value).strip().lower())
    except ValueError:
        return ConfidenceLevel.LOW


class ObjectClassIndex:
    """
    Resolves class names mentioned anywhere in the pipeline back to extracted classes.

    Parsing is per item on purpose: one malformed entry in ``objectClassesOutput`` must
    cost that entry, not the whole run.
    """

    def __init__(self, classes: Sequence[ObjectClassInfo]):
        self._classes = list(classes)
        self._by_key: Dict[str, ObjectClassInfo] = {}
        for info in self._classes:
            key = canonical_object_class_key(info.name)
            if key:
                self._by_key.setdefault(key, info)

    @classmethod
    def from_payload(cls, payload: Any) -> Tuple["ObjectClassIndex", int]:
        """Build the index from ``objectClassesOutput``; returns the index and the skipped count."""
        raw_classes: Any = None
        if isinstance(payload, Mapping):
            raw_classes = payload.get("objectClasses") or payload.get("object_classes")
        elif isinstance(payload, list):
            raw_classes = payload

        if not isinstance(raw_classes, list):
            return cls([]), 0

        parsed: List[ObjectClassInfo] = []
        skipped = 0
        for order, item in enumerate(raw_classes):
            if not isinstance(item, Mapping):
                skipped += 1
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                skipped += 1
                continue
            parsed.append(
                ObjectClassInfo(
                    name=name,
                    description=str(item.get("description") or "").strip(),
                    confidence=_coerce_confidence(item.get("confidence")),
                    embedded=is_true(item.get("embedded")),
                    abstract=is_true(item.get("abstract")),
                    superclass=str(item.get("superclass") or "").strip(),
                    order=order,
                )
            )
        return cls(parsed), skipped

    def __len__(self) -> int:
        return len(self._classes)

    @property
    def all(self) -> List[ObjectClassInfo]:
        return list(self._classes)

    def resolve(self, raw_name: Any) -> Optional[ObjectClassInfo]:
        """Resolve a mentioned name to an extracted class, tolerating only case and whitespace.

        Identifiers are treated as opaque: no singularization and no word removal, because
        those rules are English-specific and would merge distinct classes in an unrelated
        API. A mention that does not match an extracted name exactly is left unresolved and
        counted by :func:`group_observations`.
        """
        text = str(raw_name or "").strip()
        if not text:
            return None
        return self._by_key.get(canonical_object_class_key(text))

    def candidates(self, *, max_confidence_rank: int, limit: int) -> List[ObjectClassInfo]:
        """Classes worth spending an LLM sweep on: confident, concrete, ordered by rank."""
        selected = [
            info
            for info in self._classes
            if info.confidence_rank <= max_confidence_rank and not info.embedded and not info.abstract
        ]
        selected.sort(key=lambda info: (info.confidence_rank, info.order))
        return selected[:limit] if limit > 0 else selected

    def to_prompt_payload(self, classes: Sequence[ObjectClassInfo]) -> List[Dict[str, Any]]:
        """Structured class list for prompts.

        The explicit whitelist is also a prompt-safety boundary: persistence-only fields such
        as ``relevantDocumentations``, document ids and chunk ids never enter the LLM context.

        Structural flags travel with the name because they are the two largest sources of
        false positives: an embedded complex type and a superclass both look exactly like a
        relation when only the name and description are visible.

        Descriptions are truncated because this list is re-sent on every per-chunk call.
        """
        limit = config.digester.relation_prompt_max_description_chars
        return [
            {
                "name": info.name,
                "description": info.description[:limit],
                "confidence": info.confidence.value,
                "embedded": info.embedded,
                "abstract": info.abstract,
                "superclass": info.superclass,
            }
            for info in classes
        ]


# --- Pair keys and grouping ---


def pair_key(class_a: str, class_b: str) -> str:
    """Orientation-independent key for a class pair."""
    left, right = sorted((normalize_object_class_name(class_a), normalize_object_class_name(class_b)))
    return f"{left}|{right}"


@dataclass
class ObservedPair:
    """Every observation collected for one unordered class pair."""

    key: str
    class_a: str
    class_b: str
    observations: List[RelationObservation] = field(default_factory=list)
    sources: List[EvidenceSource] = field(default_factory=list)
    chunk_refs: List[Dict[str, str]] = field(default_factory=list)

    def add(
        self,
        observation: RelationObservation,
        source: EvidenceSource,
        refs: Optional[Sequence[Dict[str, str]]] = None,
    ) -> None:
        self.observations.append(observation)
        if source not in self.sources:
            self.sources.append(source)
        for chunk_ref in refs or ():
            if chunk_ref not in self.chunk_refs:
                self.chunk_refs.append(chunk_ref)

    def evidence_strength(self) -> Tuple[int, int, int, int]:
        """How well supported this pair is, for ordering when a stage has to pick a subset.

        Deterministic evidence outranks prose because it comes from an already-validated
        schema. Attribute breadth ranks next: a pair whose evidence names several distinct
        attributes on one side probably carries several associations, so dropping it costs
        more than dropping a pair with repeated evidence for a single attribute pairing.
        """
        deterministic = sum(
            1 for observation in self.observations if observation.evidence_kind in DETERMINISTIC_EVIDENCE_KINDS
        )
        side_a, side_b = self.attributes_per_side()
        return (
            deterministic,
            max(len(side_a), len(side_b)),
            int(bool(side_a) and bool(side_b)),
            len(self.observations),
        )

    @property
    def is_self_pair(self) -> bool:
        return normalize_object_class_name(self.class_a) == normalize_object_class_name(self.class_b)

    def attributes_per_side(self) -> Tuple[List[str], List[str]]:
        """Distinct attribute names observed on each side, in first-seen order.

        One pair can carry several associations - a user may be both a member and an owner of
        a group - and each association is its own pair of reference attributes. Tracking the
        attributes per side, rather than a single "both sides seen" flag, is what lets the
        later stages notice that one side names more attributes than the other and that some
        association is therefore still only half-observed.
        """
        left = normalize_object_class_name(self.class_a)
        side_a: List[str] = []
        side_b: List[str] = []

        def record(bucket: List[str], attribute: str) -> None:
            name = attribute.strip()
            if name and name not in bucket:
                bucket.append(name)

        for observation in self.observations:
            source_is_a = normalize_object_class_name(observation.source_class) == left
            record(side_a if source_is_a else side_b, observation.source_attribute)
            record(side_b if source_is_a else side_a, observation.target_attribute)
        return side_a, side_b

    def has_both_sides(self) -> bool:
        """True when some observation names an attribute on each side of the pair."""
        side_a, side_b = self.attributes_per_side()
        return bool(side_a) and bool(side_b)

    def is_weak(self) -> bool:
        """Pairs worth a second, focused look before they are judged.

        Weak means the picture is incomplete, not that it is untrustworthy. Three ways it can
        be incomplete:

        * a single observation, so nothing corroborates it;
        * no attribute named on one of the sides;
        * more distinct attributes on one side than the other, which is the signature of a
          second association that has only been seen from one end. ``Group.members`` and
          ``Group.owners`` against a lone ``User.groups`` means the ownership association is
          still missing its subject side, even though membership looks complete.
        """
        if self.is_self_pair:
            return False
        if len(self.observations) <= 1:
            return True
        side_a, side_b = self.attributes_per_side()
        if not side_a or not side_b:
            return True
        return len(side_a) != len(side_b)


ObservationEntry = Tuple[RelationObservation, EvidenceSource, Optional[Sequence[Dict[str, str]]]]
"""One observation plus the stage that produced it and the chunks backing it."""


def expand_link_object_pairs(
    entries: Sequence[ObservationEntry],
    index: ObjectClassIndex,
    attributes_by_class: Mapping[str, Any],
) -> Tuple[List[ObservationEntry], List[str]]:
    """
    Connect the two ends of an association class, which no other stage ever does.

    A membership modelled as its own class - ``Membership.user -> User`` and
    ``Membership.group -> Group`` plus a few qualifying attributes - produces observations
    that fold onto the pairs ``Membership|User`` and ``Group|Membership``. The pair the
    domain actually needs, ``User|Group``, is never formed, and because adjudication may only
    name classes belonging to the pair it is judging, no call can return ``kind=link_object``
    with ``linkObjectClass=Membership``. The evidence has to be reshaped before the LLM sees
    it; a prompt cannot recover a pair that was never created.

    Runs over observations from every stage, not just deterministic seeding, so a link object
    described only in prose is expanded too.

    Returns the synthetic entries and one human-readable line per expansion for logging.
    """
    max_pairs = config.digester.relation_link_object_max_expanded_pairs
    if max_pairs <= 0:
        return [], []

    outgoing: Dict[str, Dict[str, List[RelationObservation]]] = {}
    for observation, _source, _refs in entries:
        if observation.evidence_kind in NON_REFERENCE_EVIDENCE_KINDS:
            continue
        source = index.resolve(observation.source_class)
        target = index.resolve(observation.target_class)
        if source is None or target is None:
            continue
        if normalize_object_class_name(source.name) == normalize_object_class_name(target.name):
            continue
        outgoing.setdefault(source.name, {}).setdefault(target.name, []).append(observation)

    candidates: List[Tuple[float, str, Dict[str, List[RelationObservation]]]] = []
    for link_class, targets in outgoing.items():
        if len(targets) < 2:
            continue
        info = index.resolve(link_class)
        if info is None or info.embedded or info.abstract:
            continue
        ratio = _reference_ratio(link_class, targets, attributes_by_class)
        if ratio is not None and ratio < config.digester.relation_link_object_min_reference_ratio:
            continue
        candidates.append((ratio if ratio is not None else 0.0, link_class, targets))

    # Most reference-dense classes first: they are the likeliest association classes, so if the
    # ceiling bites it drops the weakest candidates rather than an arbitrary tail.
    candidates.sort(key=lambda item: (-item[0], item[1]))

    synthetic: List[ObservationEntry] = []
    summary: List[str] = []
    skipped = 0
    for ratio, link_class, targets in candidates:
        ends = sorted(targets, key=normalize_object_class_name)
        for position, end_a in enumerate(ends):
            for end_b in ends[position + 1 :]:
                if len(synthetic) >= max_pairs:
                    skipped += 1
                    continue
                synthetic.append(_link_object_entry(link_class, end_a, end_b, targets))
                summary.append(f"{end_a}|{end_b} via {link_class}")

    if skipped:
        summary.append(f"{skipped} further association-class pair(s) skipped by the expansion ceiling")
    return synthetic, summary


def _reference_ratio(
    link_class: str,
    targets: Mapping[str, List[RelationObservation]],
    attributes_by_class: Mapping[str, Any],
) -> Optional[float]:
    """Share of the class's attributes that point at other object classes, or None if unknown."""
    payload = attributes_by_class.get(normalize_object_class_name(link_class))
    attributes = select_attributes_map(payload)
    if not attributes:
        return None

    referencing = {
        observation.source_attribute.strip()
        for observations in targets.values()
        for observation in observations
        if observation.source_attribute.strip()
    }
    return len(referencing) / len(attributes)


def _link_object_entry(
    link_class: str,
    end_a: str,
    end_b: str,
    targets: Mapping[str, List[RelationObservation]],
) -> ObservationEntry:
    """Build the observation that connects two ends of an association class.

    Both attribute names are left empty on purpose: ``Membership.user`` is an attribute of
    Membership, not of User, so naming it here would hand adjudication an attribute that the
    grounding check would rightly reject.
    """

    def describe(end: str) -> str:
        for observation in targets.get(end, []):
            if observation.source_attribute.strip():
                return f"{link_class}.{observation.source_attribute.strip()} -> {end}"
        return f"{link_class} -> {end}"

    observation = RelationObservation(
        source_class=end_a,
        target_class=end_b,
        source_attribute="",
        target_attribute="",
        multi_valued=None,
        evidence_kind="schema_reference",
        quote=f"{describe(end_a)}; {describe(end_b)}",
        note=f"Connected only through {link_class}, which references both ends.",
        via_class=link_class,
    )
    return (observation, "link_object_expansion", None)


def group_observations(
    entries: Iterable[Tuple[RelationObservation, EvidenceSource, Optional[Sequence[Dict[str, str]]]]],
    index: ObjectClassIndex,
) -> Tuple[Dict[str, ObservedPair], int]:
    """
    Fold observations onto unordered class pairs, dropping ones whose classes are unknown.

    Returns the pairs and the number of observations discarded because a side could not be
    resolved to an extracted object class. Resolution happens here rather than in the
    prompts so that a relation pointing at a low-ranked class is kept (it is evidence the
    ranking was wrong) while a hallucinated class name is not.
    """
    pairs: Dict[str, ObservedPair] = {}
    unresolved = 0

    for observation, source, refs in entries:
        source_info = index.resolve(observation.source_class)
        target_info = index.resolve(observation.target_class)
        if source_info is None or target_info is None:
            unresolved += 1
            logger.debug(
                "[Digester:Relations] Dropping observation with unresolved class: %s -> %s",
                observation.source_class,
                observation.target_class,
            )
            continue

        normalized = observation.model_copy(update={"source_class": source_info.name, "target_class": target_info.name})
        key = pair_key(source_info.name, target_info.name)
        pair = pairs.get(key)
        if pair is None:
            class_a, class_b = sorted((source_info.name, target_info.name), key=normalize_object_class_name)
            pair = ObservedPair(key=key, class_a=class_a, class_b=class_b)
            pairs[key] = pair
        pair.add(normalized, source, refs)

    return pairs, unresolved


# --- Deterministic observation sources ---


def select_attributes_map(payload: Any) -> Dict[str, Any]:
    """Pick the attribute map out of a stored attributes payload.

    Accepts both the wrapped extraction shape ``{"attributes": {...}}`` and the direct map
    a PUT override produces.
    """
    if not isinstance(payload, Mapping):
        return {}
    attributes = payload.get("attributes")
    if isinstance(attributes, Mapping):
        return dict(attributes)
    if "attributes" in payload:
        return {}
    return {key: value for key, value in payload.items() if isinstance(value, Mapping)}


def observations_from_attributes(
    object_class: str,
    attributes_payload: Any,
    index: ObjectClassIndex,
) -> List[RelationObservation]:
    """
    Derive observations from an object class's extracted attribute schema.

    ``format`` already carries the distinction relation detection needs: ``reference``
    means the attribute points at another object class, ``embedded`` means it is a complex
    attribute of this one. Both are recorded - the embedded case as a rejection signal, so
    it is not re-proposed as a relation by a later stage.

    A self-target is not excluded. ``Group.parentGroup -> Group`` and ``User.manager -> User``
    are ordinary hierarchical associations, and this schema evidence is the strongest signal
    the pipeline has for them; the class sweep that would otherwise be their only source is
    capped, confidence-filtered and skipped for classes without mapped documentation.
    Recursion is classified by ``format`` like any other target, so recursive embedded
    structures still land as ``embedded_metadata`` and are rejected explicitly.
    """
    observations: List[RelationObservation] = []
    for attribute_name, raw_attribute in select_attributes_map(attributes_payload).items():
        if not isinstance(raw_attribute, Mapping):
            continue
        # Exact match first: the reference marker is a producer convention, not a naming rule,
        # so a class actually named "Reference <something>" must not be collapsed into <something>.
        raw_type = raw_attribute.get("type")
        target = index.resolve(raw_type) or index.resolve(dereference_attribute_type(raw_type))
        if target is None:
            continue

        attribute_format = str(raw_attribute.get("format") or "").strip().lower()
        multivalue = raw_attribute.get("multivalue")
        description = str(raw_attribute.get("description") or "").strip()

        if attribute_format == "embedded" or target.embedded:
            evidence_kind = "embedded_metadata"
        else:
            evidence_kind = "attribute_metadata"

        observations.append(
            RelationObservation(
                source_class=object_class,
                target_class=target.name,
                source_attribute=str(attribute_name).strip(),
                target_attribute="",
                multi_valued=is_true(multivalue) if multivalue is not None else None,
                evidence_kind=evidence_kind,
                quote=f"{object_class}.{attribute_name}: type={target.name} format={attribute_format or 'unknown'}",
                note=description[:200],
            )
        )
    return observations


def observations_from_endpoints(
    object_class: str,
    endpoints_payload: Any,
    index: ObjectClassIndex,
) -> List[RelationObservation]:
    """
    Derive observations from sub-resource endpoint paths such as ``/ClassA/{id}/ClassB``.

    Only paths whose both literal segments resolve to extracted object classes are used.
    A path whose tail names an attribute rather than a known class is left to the
    attribute-derived and LLM stages instead of being guessed at.

    ``/Group/{id}/Group`` is kept: a sub-resource path from a class to itself is exactly the
    API surface a hierarchical association is exposed through, and dropping it would leave
    the recursive case to the capped class sweep alone.
    """
    if not isinstance(endpoints_payload, Mapping):
        return []
    endpoints = endpoints_payload.get("endpoints")
    if not isinstance(endpoints, list):
        return []

    observations: List[RelationObservation] = []
    seen: set[Tuple[str, str, str]] = set()
    for endpoint in endpoints:
        if not isinstance(endpoint, Mapping):
            continue
        path = str(endpoint.get("path") or "").strip()
        if not path:
            continue
        segments = [segment for segment in path.split("/") if segment]
        for position in range(len(segments) - 2):
            head, parameter, tail = segments[position], segments[position + 1], segments[position + 2]
            if not _PATH_PARAMETER_RE.match(parameter):
                continue
            head_info = index.resolve(head)
            tail_info = index.resolve(tail)
            if head_info is None or tail_info is None:
                continue
            # Keyed on the path, not just the class pair: two sub-resource surfaces between the
            # same classes are usually two associations, and the path is the only role-bearing
            # evidence an endpoint observation carries.
            dedup_key = (head_info.name, tail_info.name, path)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            observations.append(
                RelationObservation(
                    source_class=head_info.name,
                    target_class=tail_info.name,
                    source_attribute="",
                    target_attribute="",
                    multi_valued=None,
                    evidence_kind="endpoint_path",
                    quote=f"{str(endpoint.get('method') or 'GET').upper()} {path}",
                    note="Sub-resource path exposes the link as an API surface.",
                )
            )
    return observations


def observations_from_class_metadata(index: ObjectClassIndex) -> List[RelationObservation]:
    """Record declared inheritance so adjudication can reject it explicitly."""
    observations: List[RelationObservation] = []
    for info in index.all:
        if not info.superclass:
            continue
        parent = index.resolve(info.superclass)
        if parent is None or normalize_object_class_name(parent.name) == normalize_object_class_name(info.name):
            continue
        observations.append(
            RelationObservation(
                source_class=info.name,
                target_class=parent.name,
                source_attribute="",
                target_attribute="",
                multi_valued=None,
                evidence_kind="inheritance_metadata",
                quote=f"{info.name} extends {parent.name}",
                note="Schema inheritance, not an association.",
            )
        )
    return observations


# --- Projection ---


def default_relation_name(subject: str, object_class: str, subject_attribute: str = "") -> str:
    """``{subject}_to_{object}``, suffixed when the same pair is reached by several attributes."""
    base = f"{normalize_object_class_name(subject)}_to_{normalize_object_class_name(object_class)}"
    base = "_".join(split_relation_tokens(base))
    suffix = "_".join(split_relation_tokens(subject_attribute)) if subject_attribute else ""
    return f"{base}_via_{suffix}" if suffix else base


def verdict_to_relation_record(verdict: RelationVerdict) -> Optional[RelationRecord]:
    """
    Project an accepted verdict onto the midPoint-facing record.

    Everything the verdict knows beyond these seven fields (kind, cardinality, link class,
    confidence, rationale) stays in the persisted analysis; the API contract is unchanged.
    """
    subject = normalize_object_class_name(verdict.subject)
    object_class = normalize_object_class_name(verdict.object)
    if not subject or not object_class:
        return None

    # The `_via_<attribute>` form is reserved for disambiguating several relations between the
    # same pair, which deduplicate_relation_names applies only where a collision actually occurs.
    name = "_".join(split_relation_tokens(verdict.name)) or default_relation_name(subject, object_class)
    display_name = verdict.display_name.strip() or f"{subject.title()} to {object_class.title()}"

    return RelationRecord(
        name=name,
        display_name=display_name,
        short_description=verdict.short_description.strip(),
        subject=subject,
        subject_attribute=verdict.subject_attribute.strip(),
        object=object_class,
        object_attribute=verdict.object_attribute.strip(),
    )


def sort_relations_by_iga_priority(
    relations: Sequence[RelationRecord],
    index: ObjectClassIndex,
) -> List[RelationRecord]:
    """Keep the established subject-first ordering from ``objectClassesOutput``.

    The object-class extraction result is already ranked by IGA relevance. Relation order
    is part of the response's established semantics, so the staged detector must not replace
    it with LLM verdict confidence or alphabetical order.
    """
    if len(relations) <= 1:
        return list(relations)

    missing_priority = (len(CONFIDENCE_RANK), len(index.all) + 1)

    def class_priority(name: str) -> Tuple[int, int]:
        info = index.resolve(name)
        return (info.confidence_rank, info.order) if info else missing_priority

    return sorted(
        relations,
        key=lambda relation: (
            *class_priority(relation.subject),
            *class_priority(relation.object),
            normalize_object_class_name(relation.subject),
            normalize_object_class_name(relation.subject_attribute or ""),
            normalize_object_class_name(relation.object),
            normalize_object_class_name(relation.object_attribute or ""),
            relation.name,
        ),
    )


def disambiguate_relation_labels(relations: List[RelationRecord]) -> List[RelationRecord]:
    """Ensure two relations between the same classes never carry the same display name.

    The adjudication prompt asks for role-bearing labels, but a lazy answer would leave a
    reviewer with two entries both reading "User to Group". Renaming only the machine
    identifier does not help them; the human-facing label has to differ too, so the
    distinguishing attribute is appended when the model did not distinguish them itself.
    """
    by_label: Dict[Tuple[str, str, str], List[RelationRecord]] = {}
    for relation in relations:
        key = (
            normalize_object_class_name(relation.subject),
            normalize_object_class_name(relation.object),
            relation.display_name.strip().casefold(),
        )
        by_label.setdefault(key, []).append(relation)

    adjusted: Dict[int, RelationRecord] = {}
    for group in by_label.values():
        if len(group) < 2:
            continue
        for relation in group:
            marker = (relation.subject_attribute or relation.object_attribute or "").strip()
            if not marker:
                marker = relation.name
            if not marker:
                continue
            adjusted[id(relation)] = relation.model_copy(
                update={"display_name": f"{relation.display_name} via {marker}".strip()}
            )
            logger.info(
                "[Digester:Relations] Disambiguated the display name of relation %s on %s -> %s",
                relation.name,
                relation.subject,
                relation.object,
            )

    return [adjusted.get(id(relation), relation) for relation in relations]


def deduplicate_relation_names(relations: List[RelationRecord]) -> List[RelationRecord]:
    """
    Guarantee unique ``name`` values.

    The codegen route resolves a relation by name, so two records sharing one makes the
    second unreachable. Collisions are suffixed rather than dropped.
    """
    seen: Dict[str, int] = {}
    unique: List[RelationRecord] = []
    for relation in relations:
        base = relation.name or default_relation_name(relation.subject, relation.object)
        count = seen.get(base, 0)
        seen[base] = count + 1
        if count == 0:
            unique.append(relation.model_copy(update={"name": base}))
            continue
        suffix = "_".join(split_relation_tokens(relation.subject_attribute or relation.object_attribute or ""))
        candidate = f"{base}_via_{suffix}" if suffix else f"{base}_{count + 1}"
        while candidate in seen:
            count += 1
            candidate = f"{base}_{count + 1}"
        seen[candidate] = 1
        logger.info("[Digester:Relations] Renamed duplicate relation %s to %s", base, candidate)
        unique.append(relation.model_copy(update={"name": candidate}))
    return unique


def grounded_attribute_names(attributes_payload: Any) -> set[str]:
    """Canonical attribute names of one class, for checking that a verdict cites real ones."""
    return {
        "".join(split_relation_tokens(str(name)))
        for name in select_attributes_map(attributes_payload)
        if str(name).strip()
    }


def is_attribute_grounded(attribute: str, known_attributes: set[str]) -> bool:
    """True when the name matches a known attribute, ignoring case and separators."""
    if not attribute.strip():
        return True
    if not known_attributes:
        return True
    return "".join(split_relation_tokens(attribute)) in known_attributes
