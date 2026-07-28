# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Normalization of extracted-artifact identifiers (object classes, SCIM paths, endpoints).

Generic payload normalizers with no domain meaning stay in ``src.shared.normalize``;
these helpers encode connector-domain naming rules and are consumed by the
documents toolkit and the feature modules.
"""

import re
from typing import Any


def normalize_object_class_name(object_class: str) -> str:
    """Normalize object class name for case-insensitive matching."""
    return object_class.strip().lower()


def canonical_object_class_key(name: str) -> str:
    """Whitespace-insensitive key for grouping object-class name variants.

    Unlike :func:`normalize_object_class_name` (used for result-key matching, where
    whitespace must be preserved), this also removes all internal whitespace so that
    e.g. ``"Service Account"`` and ``"ServiceAccount"`` collapse to the same dedup key.
    """
    return "".join(normalize_object_class_name(name).split())


def canonicalize_scim_path(scim_path: Any) -> str:
    """Convert quoted bracket property access to canonical SCIM dot notation."""
    normalized = str(scim_path or "").strip()
    return re.sub(r"\[['\"]([A-Za-z_$][A-Za-z0-9_$-]*)['\"]\]", r".\1", normalized)


def normalize_scim_path_for_lookup(scim_path: Any) -> str:
    """Normalize a SCIM path for matching protocol and provider mappings.

    Provider documentation often selects one item from a multi-valued attribute,
    for example ``emails[0].value`` or ``emails[type eq 'work'].value``. SCIM
    schema and ConnID projections normally expose the corresponding canonical
    path ``emails.value``. Extension URNs are kept intact apart from casing.
    """
    normalized = canonicalize_scim_path(scim_path)
    if normalized.startswith("urn:"):
        return normalized.lower()
    return re.sub(r"\[[^\]]*\]", "", normalized).lower()


def normalize_endpoint_key(path: Any, method: Any) -> tuple[str, str] | None:
    """Build normalized endpoint key from path + method."""
    path_str = str(path or "").strip()
    method_str = str(method or "").strip().upper()
    if not path_str or not method_str:
        return None
    return path_str, method_str
