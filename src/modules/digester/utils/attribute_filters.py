# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import re
from typing import Any, Dict, Mapping

_CUSTOM_FIELD_PATTERN = re.compile(r"^customfield(?:\d+)?$", re.IGNORECASE)


def ignore_attribute_name(attribute_name: str) -> bool:
    normalized_name = attribute_name.strip()
    normalized_lower = normalized_name.casefold()

    if normalized_name.startswith("_"):
        return True

    if _CUSTOM_FIELD_PATTERN.fullmatch(normalized_name):
        return True

    return normalized_lower in {"mail", "identityurl"}


def filter_ignored_attributes(attributes: Mapping[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {name: info for name, info in attributes.items() if not ignore_attribute_name(name)}


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
