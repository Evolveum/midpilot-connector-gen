# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Canonical media types for midPoint connector-development (conndev) schema uploads.

A conndev document is a connector schema exported from midPoint (one object class /
one schema per document). Both the short and the Evolveum-namespaced spellings are
accepted for backward compatibility. This is the single source of truth shared by the
upload/ingest path and the digester read path so the two cannot drift apart.
"""

# JSON variants (the SCIM/connector schema is JSON).
CONNDEV_JSON_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "application/conndev+json",
        "application/com.evolveum.conndev+json",
    }
)

# YAML variants (accepted on upload; the same schema expressed as YAML).
CONNDEV_YAML_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "application/conndev+yaml",
        "application/com.evolveum.conndev+yaml",
    }
)

# All conndev media types (JSON + YAML).
CONNDEV_CONTENT_TYPES: frozenset[str] = CONNDEV_JSON_CONTENT_TYPES | CONNDEV_YAML_CONTENT_TYPES

# Default media type assigned to a ``.conndev`` upload that carries no explicit content type.
DEFAULT_CONNDEV_CONTENT_TYPE = "application/com.evolveum.conndev+json"

# File suffix midPoint connector schemas are uploaded with.
CONNDEV_SUFFIX = ".conndev"


def normalize_content_type(content_type: str | None) -> str:
    """Canonicalize a media type for comparison: drop parameters, trim, lower-case."""
    return (content_type or "").split(";", 1)[0].strip().lower()


def is_conndev_content_type(content_type: str | None) -> bool:
    """True when ``content_type`` is any accepted conndev media type (JSON or YAML)."""
    return normalize_content_type(content_type) in CONNDEV_CONTENT_TYPES


def is_conndev_json_content_type(content_type: str | None) -> bool:
    """True when ``content_type`` is a JSON conndev media type."""
    return normalize_content_type(content_type) in CONNDEV_JSON_CONTENT_TYPES
