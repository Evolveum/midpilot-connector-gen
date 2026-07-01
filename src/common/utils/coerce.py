# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Defensive coercion helpers for untrusted payloads (LLM output, stored JSON, DB rows).

Use these at the boundary where data of unknown shape enters the code, so the rest of the
logic can work with a guaranteed type instead of repeating ``value if isinstance(...) else
<default>`` everywhere. Each helper returns a safe empty default when the value has the wrong
type and never raises.

These are coercion helpers (substitute a default), not filters: ``as_mapping(x)`` yields ``{}``
for a bad value, it does not "skip" it. For per-item validation inside a loop keep an explicit
``isinstance`` guard.
"""

from collections.abc import Mapping
from typing import Any


def as_mapping(value: Any) -> Mapping[str, Any]:
    """Return ``value`` if it is a mapping, otherwise an empty mapping."""
    return value if isinstance(value, Mapping) else {}


def as_list(value: Any) -> list[Any]:
    """Return ``value`` if it is a list, otherwise an empty list."""
    return value if isinstance(value, list) else []


def as_str(value: Any) -> str:
    """Return ``value`` if it is a string, otherwise an empty string."""
    return value if isinstance(value, str) else ""


def as_str_list(value: Any) -> list[str]:
    """Return the string items of ``value`` when it is a list, otherwise an empty list."""
    return [item for item in as_list(value) if isinstance(item, str)]


def as_dict_list(value: Any) -> list[dict[str, Any]]:
    """Return the dict items of ``value`` when it is a list, otherwise an empty list.

    Handy for the common "iterate a list, keep only the object entries" pattern over
    untrusted payloads (replaces a list guard plus a per-item ``isinstance(x, dict)`` skip).
    """
    return [item for item in as_list(value) if isinstance(item, dict)]
