# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import re
from typing import Dict, List, Optional, Tuple

from src.common.utils.normalize import normalize_object_class_name
from src.config import config
from src.modules.digester.schema import RelationRecord


def split_relation_tokens(value: str) -> List[str]:
    """Split relation labels/attributes into stable lowercase tokens."""
    with_spaces = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value.strip())
    return [token.lower() for token in re.sub(r"[^A-Za-z0-9]+", " ", with_spaces).split() if token]


def _singularize_relation_token(token: str) -> str:
    if token.endswith("ies") and len(token) > 4:
        return f"{token[:-3]}y"
    if token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        return token[:-1]
    return token


def _generic_attribute_tokens() -> set[str]:
    return {token.casefold() for token in config.digester.relation_generic_attribute_tokens}


def canonical_relation_attribute(value: Optional[str]) -> str:
    """
    Normalize relation attribute wording for duplicate detection.

    This intentionally removes generic relation words so variants such as
    `hasMembership`, `membershipIds`, and `membership` collapse to the same key.
    """
    generic_tokens = _generic_attribute_tokens()
    tokens = [
        _singularize_relation_token(token)
        for token in split_relation_tokens(value or "")
        if token not in generic_tokens
    ]
    return " ".join(tokens)


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


def _relation_semantic_key(relation: RelationRecord) -> Tuple[str, str, str, str]:
    return (
        normalize_object_class_name(relation.subject),
        normalize_object_class_name(relation.object),
        canonical_relation_attribute(relation.subject_attribute),
        canonical_relation_attribute(relation.object_attribute),
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

    The LLM sometimes emits both `user has membership` and `user to membership`.
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
