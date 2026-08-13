# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import re
from typing import Dict, List, Optional, Tuple

from src.documents.normalize import normalize_object_class_name
from src.modules.digester.schemas import RelationRecord


def split_relation_tokens(value: str) -> List[str]:
    """Split relation labels/attributes into stable lowercase tokens."""
    with_spaces = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value.strip())
    return [token.lower() for token in re.sub(r"[^A-Za-z0-9]+", " ", with_spaces).split() if token]


def canonical_relation_attribute(value: Optional[str]) -> str:
    """
    Normalize separators and casing for duplicate detection without interpreting words.

    Attribute vocabulary is application-specific. Removing English words or applying
    English singularization can merge two distinct attributes from an unrelated API, so
    semantic deduplication is left to the adjudication LLM.
    """
    return " ".join(split_relation_tokens(value or ""))


def _normalize_relation_id(value: str) -> str:
    return "_".join(split_relation_tokens(value))


def _attribute_preference_key(value: str) -> Tuple[bool, int, int, str]:
    stripped = value.strip()
    canonical = canonical_relation_attribute(stripped)
    return (
        bool(canonical),
        -len(split_relation_tokens(stripped)),
        -len(stripped),
        stripped.lower(),
    )


def _select_preferred_attribute(values: List[Optional[str]]) -> str:
    non_empty_values = [value.strip() for value in values if value and value.strip()]
    if not non_empty_values:
        return ""
    return max(non_empty_values, key=_attribute_preference_key)


def relation_identity(
    subject: str,
    object_class: str,
    subject_attribute: Optional[str],
    object_attribute: Optional[str],
) -> Tuple[str, str, str, str]:
    """
    Canonical identity of one association: the class pair plus the attributes that distinguish it.

    One class pair can carry several associations, so the pair alone does not identify one.
    The attribute names do, once separators and casing are normalized away - which is what
    lets a projected ``RelationRecord`` be joined back onto the verdict it came from, even
    after duplicate merging swapped one raw spelling of an attribute for another.
    """
    return (
        normalize_object_class_name(subject),
        normalize_object_class_name(object_class),
        "".join(split_relation_tokens(subject_attribute or "")),
        "".join(split_relation_tokens(object_attribute or "")),
    )


def _relation_semantic_key(relation: RelationRecord) -> Tuple[str, str, str, str]:
    """Key that collapses wording-only duplicates of the same association.

    The attribute pair is what distinguishes two associations between the same classes - a
    user that is both a member and an owner of a group holds two of them. When neither side
    names an attribute there is nothing structural left to tell them apart, so the relation
    identifier stands in: it was assigned by the stage that saw all the evidence, and two
    differently named entries from that stage are a deliberate distinction, not a duplicate.
    """
    subject_attribute = canonical_relation_attribute(relation.subject_attribute)
    object_attribute = canonical_relation_attribute(relation.object_attribute)
    if not subject_attribute and not object_attribute:
        subject_attribute = _normalize_relation_id(relation.name)
    return (
        normalize_object_class_name(relation.subject),
        normalize_object_class_name(relation.object),
        subject_attribute,
        object_attribute,
    )


def _relation_preference_key(relation: RelationRecord) -> Tuple[bool, bool, bool, bool, int, int]:
    subject_key = normalize_object_class_name(relation.subject)
    object_key = normalize_object_class_name(relation.object)
    default_name = f"{subject_key}_to_{object_key}"
    return (
        _normalize_relation_id(relation.name) == default_name,
        bool((relation.subject_attribute or "").strip()),
        bool((relation.object_attribute or "").strip()),
        bool((relation.display_name or "").strip()),
        len(relation.short_description or ""),
        -len(relation.name or ""),
    )


def merge_duplicate_relation(left: RelationRecord, right: RelationRecord) -> RelationRecord:
    """
    Merge wording-only duplicates while preserving the richest metadata.

    The LLM can emit two differently worded labels for the same association.
    In ConnId/midPoint relationship terms those are one subject->object association;
    the difference belongs in the relation label, not in a second relation record.
    """
    preferred = max([left, right], key=_relation_preference_key).model_copy(deep=True)
    preferred.subject_attribute = _select_preferred_attribute([left.subject_attribute, right.subject_attribute])
    preferred.object_attribute = _select_preferred_attribute([left.object_attribute, right.object_attribute])
    return preferred


def deduplicate_semantic_relations(relations: List[RelationRecord]) -> List[RelationRecord]:
    if len(relations) <= 1:
        return list(relations)

    deduplicated: Dict[Tuple[str, str, str, str], RelationRecord] = {}
    for relation in relations:
        dedup_key = _relation_semantic_key(relation)
        current = deduplicated.get(dedup_key)
        if current is None:
            deduplicated[dedup_key] = relation
            continue
        deduplicated[dedup_key] = merge_duplicate_relation(current, relation)

    return list(deduplicated.values())
