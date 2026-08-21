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

# The protocol discriminator of every conndev export: the binding key present on an object-class
# document or on one of its attribute shadows. Single source of truth for both lookups.
CONNDEV_PROTOCOL_BINDINGS: dict[str, ApiType] = {
    CONNDEV_SCIM_BINDING: ApiType.SCIM,
    CONNDEV_SQL_BINDING: ApiType.SQL,
}


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


def shadow_object_attributes(value: Any) -> Mapping[str, Any] | None:
    """Unwrap a midPoint shadow wrapper (``{"object": {"attributes": {...}}}``) to its attributes."""
    if not isinstance(value, Mapping):
        return None
    shadow_object = value.get("object")
    if not isinstance(shadow_object, Mapping):
        return None
    attributes = shadow_object.get("attributes")
    return attributes if isinstance(attributes, Mapping) else None


def conndev_attribute_shadows(attributes: Any) -> list[Any]:
    """
    Normalize a conndev ``attributes`` field to a list of attribute shadows.

    An object class with exactly one attribute is exported as a bare object rather than a
    one-element list, so a plain list guard would drop its only attribute.
    """
    if isinstance(attributes, Mapping):
        return [attributes]
    return list(attributes) if isinstance(attributes, list) else []


def _binding_protocol(container: Mapping[str, Any]) -> ApiType | None:
    """Return the protocol of the first conndev binding key present in ``container``."""
    for binding, api_type in CONNDEV_PROTOCOL_BINDINGS.items():
        if binding in container:
            return api_type
    return None


def is_conndev_object_class_document(document: Any) -> bool:
    """
    True when a conndev document is an object-class export.

    Both the protocol-bound classes (``User``) and the embedded sub-classes midPoint exports for
    complex attributes (``User__name``) carry an identifying ``uid`` together with a ``name``.
    """
    if not isinstance(document, Mapping):
        return False
    return bool(str(document.get("uid") or "").strip()) and bool(str(document.get("name") or "").strip())


def detect_conndev_object_class_api_type(document: Any) -> ApiType | None:
    """
    Return the protocol declared by a shadow-wrapped Conndev object-class export.

    The binding normally sits on the class itself. Embedded sub-classes exported for complex
    attributes (``User__name``, ``User__emails``) have no schema binding of their own, so their
    protocol is read from the ``ri:conndev_Attribute`` shadows instead. An embedded sub-class
    without attributes declares no protocol at all and yields ``None``.
    """
    if not isinstance(document, Mapping) or "uid" not in document:
        return None

    class_binding = _binding_protocol(document)
    if class_binding is not None:
        return class_binding

    for shadow in conndev_attribute_shadows(document.get("attributes")):
        attributes = shadow_object_attributes(shadow)
        if attributes is None:
            continue
        attribute_binding = _binding_protocol(attributes)
        if attribute_binding is not None:
            return attribute_binding
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
