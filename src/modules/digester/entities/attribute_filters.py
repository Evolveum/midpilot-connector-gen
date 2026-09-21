# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
import re
from typing import Any, Dict, List, Mapping

from src.modules.digester.schemas import AttributeProcessingInfo

logger = logging.getLogger(__name__)

_CUSTOM_FIELD_PATTERN = re.compile(r"^customfield(?:\d+)?$", re.IGNORECASE)
_WORD_PATTERN = re.compile(r"[A-Za-z0-9_]+")


def ignore_attribute_name(attribute_name: str) -> bool:
    normalized_name = attribute_name.strip()
    normalized_lower = normalized_name.casefold()

    if normalized_name.startswith("_"):
        return True

    if _CUSTOM_FIELD_PATTERN.fullmatch(normalized_name):
        return True

    return normalized_lower in {"mail", "identityurl"}


def filter_ignored_attributes(attributes: List[AttributeProcessingInfo]) -> List[str]:
    return [attr.name for attr in attributes if not ignore_attribute_name(attr.name)]


def normalize_readability_flags(
    attributes: Mapping[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """
    Ensure unreadable attributes are never marked as returned by default.

    Contract:
    - if readable is explicitly False, returnedByDefault must be False
    """
    normalized: Dict[str, Dict[str, Any]] = {}

    for name, info in attributes.items():
        normalized_info = dict(info)
        if normalized_info.get("readable") is False:
            normalized_info["returnedByDefault"] = False
        normalized[name] = normalized_info

    return normalized


def to_lower_camel_case(name: str) -> str:
    """
    Convert a multi-word attribute name to lowerCamelCase.

    Splits on separators that make a name unusable as a connector code identifier
    (spaces, hyphens, ...) so a documentation-derived label like "Cost Center" becomes
    "costCenter". Underscore is kept as part of a word rather than treated as a
    separator: snake_case names such as "created_at" are already valid identifiers and
    are returned unchanged. A name with only one word is likewise returned unchanged:
    the reported problem is unusable multi-word names, not single-word casing style.
    """
    words = _WORD_PATTERN.findall(name)
    if len(words) <= 1:
        return name.strip()
    head, *tail = words
    return head[:1].lower() + head[1:] + "".join(word[:1].upper() + word[1:] for word in tail)


def normalize_attribute_name_casing(
    attributes: Mapping[str, Dict[str, Any]],
    object_class: str = "",
) -> Dict[str, Dict[str, Any]]:
    """
    Rewrite attribute-name map keys to lowerCamelCase.

    Attribute names are used verbatim as connector code identifiers, so a documentation
    label containing spaces (e.g. "Cost Center") is technically valid in the extracted
    JSON but unusable once generated into code. When two names collapse onto the same
    lowerCamelCase key, the first one wins and the collision is logged rather than
    silently dropping data.
    """
    normalized: Dict[str, Dict[str, Any]] = {}

    for name, info in attributes.items():
        camel_name = to_lower_camel_case(name)
        if not camel_name:
            continue
        if camel_name in normalized:
            logger.warning(
                "[Digester:Attributes] Attribute name '%s' collapses to '%s' for %s after lowerCamelCase "
                "normalization, which already has an entry; keeping the first one",
                name,
                camel_name,
                object_class or "object class",
            )
            continue
        normalized[camel_name] = info

    return normalized
