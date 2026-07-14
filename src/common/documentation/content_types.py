# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Canonical media types for midPoint connector-development (conndev) schema uploads.

A conndev document is a connector schema, SCIM resource descriptor or connector object-class
projection exported from midPoint. The document is JSON; both the short and the
Evolveum-namespaced media types are accepted for backward compatibility. This is the single
source of truth shared by upload/ingest and digester paths so the two cannot drift apart.
"""

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


def normalize_content_type(content_type: str | None) -> str:
    """Canonicalize a media type for comparison: drop parameters, trim, lower-case."""
    return (content_type or "").split(";", 1)[0].strip().lower()


def is_conndev_content_type(content_type: str | None) -> bool:
    """True when ``content_type`` is any accepted conndev media type."""
    return normalize_content_type(content_type) in CONNDEV_CONTENT_TYPES


def is_conndev_export_filename(filename_or_url: str | None) -> bool:
    """Recognize connector-development exports without depending on an object-class name."""
    basename = str(filename_or_url or "").rsplit("/", 1)[-1].strip().lower()
    return basename.endswith(CONNDEV_SUFFIX) or (
        basename.startswith(CONNDEV_JSON_FILENAME_PREFIX) and basename.endswith(".json")
    )


def is_conndev_document(content_type: str | None, filename_or_url: str | None = None) -> bool:
    """Recognize a conndev document from its canonical media type or export filename."""
    return is_conndev_content_type(content_type) or is_conndev_export_filename(filename_or_url)
