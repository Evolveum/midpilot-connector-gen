# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Canonical media types for midPoint connector-development (conndev) schema uploads.

A conndev document is a connector schema, SCIM resource descriptor or connector object-class
projection exported from midPoint. The document is JSON; both the short and the
Evolveum-namespaced media types are accepted for backward compatibility.

Persisted documents must be selected exclusively by their metadata content type. Filename
recognition exists only for upload-time normalization and must never be used as a fallback by
digester or other DB-backed runtime paths.
"""

from collections.abc import Mapping
from typing import Any

from src.shared.enums import ApiType

# The conndev connector schema is JSON.
CONNDEV_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "application/conndev+json",
        "application/com.evolveum.conndev+json",
    }
)

# Default media type assigned to a recognized conndev export without an explicit content type.
DEFAULT_CONNDEV_CONTENT_TYPE = "application/com.evolveum.conndev+json"

# File suffix midPoint connector schemas are uploaded with.
CONNDEV_SUFFIX = ".conndev"
CONNDEV_JSON_FILENAME_PREFIX = "conndev_"
CONNDEV_SCIM_BINDING = "scim"
CONNDEV_SQL_BINDING = "sql"


def normalize_content_type(content_type: str | None) -> str:
    """Canonicalize a media type for comparison: drop parameters, trim, lower-case."""
    return (content_type or "").split(";", 1)[0].strip().lower()


def is_conndev_content_type(content_type: str | None) -> bool:
    """True when ``content_type`` is any accepted conndev media type."""
    return normalize_content_type(content_type) in CONNDEV_CONTENT_TYPES


def get_documentation_item_content_type(item: Mapping[str, Any] | None) -> str | None:
    """Read content type from normalized (``@metadata``) or repository (``metadata``) item shapes."""
    if not isinstance(item, Mapping):
        return None

    metadata = item.get("@metadata") or item.get("metadata")
    if not isinstance(metadata, Mapping):
        return None

    content_type = metadata.get("content_type")
    return content_type if isinstance(content_type, str) else None


def is_conndev_documentation_item(item: Mapping[str, Any] | None) -> bool:
    """True when a documentation item is marked with an accepted conndev content type."""
    return is_conndev_content_type(get_documentation_item_content_type(item))


def is_conndev_export_filename(filename_or_url: str | None) -> bool:
    """Recognize an upload filename so ingest can assign the canonical conndev content type."""
    basename = str(filename_or_url or "").rsplit("/", 1)[-1].strip().lower()
    return basename.endswith(CONNDEV_SUFFIX) or (
        basename.startswith(CONNDEV_JSON_FILENAME_PREFIX) and basename.endswith(".json")
    )


def detect_conndev_object_class_api_type(document: Any) -> ApiType | None:
    """Return the protocol declared by a shadow-wrapped Conndev object-class export."""
    if not isinstance(document, Mapping) or "uid" not in document:
        return None
    if CONNDEV_SCIM_BINDING in document:
        return ApiType.SCIM
    if CONNDEV_SQL_BINDING in document:
        return ApiType.SQL
    return None


def detect_conndev_api_type(document: Any) -> ApiType | None:
    """Classify any supported Conndev contract as SQL or SCIM from its JSON shape."""
    object_class_api_type = detect_conndev_object_class_api_type(document)
    if object_class_api_type is not None:
        return object_class_api_type
    if not isinstance(document, Mapping):
        return None

    # Older SCIM ConnId object-class exports predate the shadow-wrapped ``scim`` binding.
    if "locator" in document and "uid" in document:
        return ApiType.SCIM
    if "schemaContent" in document:
        return ApiType.SCIM
    if "endpoint" in document and "primarySchema" in document:
        return ApiType.SCIM

    name = str(document.get("name") or "").strip().lower()
    document_id = str(document.get("id") or "").strip().lower()
    if "content" in document and "serviceproviderconfig" in {name, document_id}:
        return ApiType.SCIM
    return None
