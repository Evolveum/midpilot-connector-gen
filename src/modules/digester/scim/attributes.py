# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
SCIM schema-to-digester attribute heuristics.
"""

import re
from typing import Any, Dict, Optional, Tuple

from src.modules.digester.schema import AttributeInfoScim
from src.modules.digester.scim.embedded import build_embedded_object_class_name
from src.modules.digester.scim.loader import load_scim_base_schemas


def _map_scim_type_to_digester(scim_type: Any) -> str:
    type_map = {
        "string": "string",
        "boolean": "boolean",
        "decimal": "number",
        "integer": "integer",
        "dateTime": "string",
        "binary": "string",
        "reference": "string",
        "complex": "object",
    }
    return type_map.get(str(scim_type or ""), "string")


def _infer_scim_format(attr: Dict[str, Any]) -> Optional[str]:
    scim_type = attr.get("type")
    if scim_type == "dateTime":
        return "date-time"
    if scim_type == "binary":
        return "binary"
    if scim_type == "reference":
        return "reference"
    if scim_type == "complex":
        return "embedded"

    attr_name = str(attr.get("name") or "").lower()
    if "email" in attr_name:
        return "email"
    if "url" in attr_name or "uri" in attr_name:
        return "uri"
    return None


def _map_scim_mutability(attr: Dict[str, Any]) -> Tuple[bool, bool, bool]:
    mutability = str(attr.get("mutability") or "readWrite")
    if mutability == "readOnly":
        return False, False, True
    if mutability == "writeOnly":
        return True, True, False
    if mutability == "immutable":
        return False, True, True
    return True, True, True


def _map_scim_returned_by_default(attr: Dict[str, Any]) -> bool:
    returned = str(attr.get("returned") or "default")
    return returned in {"always", "default"}


def map_scim_attribute_to_digester_attribute(
    attr: Dict[str, Any],
    scim_path: str,
    *,
    attribute_type: Optional[str] = None,
    attribute_format: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Map one SCIM schema attribute or sub-attribute into AttributeInfoScim.
    """
    updatable, creatable, readable = _map_scim_mutability(attr)
    attribute = AttributeInfoScim(
        type=attribute_type or _map_scim_type_to_digester(attr.get("type")),
        format=attribute_format if attribute_format is not None else _infer_scim_format(attr),
        description=str(attr.get("description") or ""),
        mandatory=bool(attr.get("required", False)),
        updatable=updatable,
        creatable=creatable,
        readable=readable,
        multivalue=bool(attr.get("multiValued", False)),
        returnedByDefault=_map_scim_returned_by_default(attr),
        scimAttribute=scim_path,
    )
    return attribute.model_dump(by_alias=True)


def _get_schema_case_insensitive(schemas: Dict[str, Any], object_class: str) -> Optional[Dict[str, Any]]:
    normalized_name = object_class.strip().lower()
    for schema_name, schema in schemas.items():
        if schema_name.strip().lower() == normalized_name and isinstance(schema, dict):
            return schema
    return None


def _get_attribute_root(scim_path: str) -> str:
    """Return the first SCIM attribute segment for a path-like SCIM attribute."""
    normalized = str(scim_path or "").strip()
    if normalized.startswith("urn:"):
        return normalized

    match = re.match(r"([A-Za-z_$][A-Za-z0-9_$-]*)", normalized)
    return match.group(1) if match else normalized


def normalize_scim_path_for_lookup(scim_path: Any) -> str:
    """
    Normalize SCIM paths for schema-baseline lookup.

    Documentation often uses indexed or filtered multi-value paths such as
    `emails[0].value` or `emails[type eq 'work'].value`, while the SCIM schema
    baseline uses the canonical sub-attribute path `emails.value`.
    """
    normalized = str(scim_path or "").strip()
    if normalized.startswith("urn:"):
        return normalized.lower()
    return re.sub(r"\[[^\]]*\]", "", normalized).lower()


def get_scim_schema_attribute_context(object_class: str) -> Optional[Dict[str, set[str]]]:
    """
    Return top-level SCIM attribute names that help scope documented mappings.
    """
    schemas = load_scim_base_schemas()
    schema = _get_schema_case_insensitive(schemas, object_class)
    if schema is None:
        return None

    current_attributes: set[str] = set()
    complex_attributes: set[str] = set()
    other_standard_attributes: set[str] = set()

    attributes = schema.get("attributes", [])
    if isinstance(attributes, list):
        for attr in attributes:
            if not isinstance(attr, dict):
                continue
            attr_name = attr.get("name")
            if not isinstance(attr_name, str) or not attr_name.strip():
                continue
            normalized_name = attr_name.strip().lower()
            current_attributes.add(normalized_name)
            if attr.get("type") == "complex":
                complex_attributes.add(normalized_name)

    normalized_object_class = object_class.strip().lower()
    for schema_name, other_schema in schemas.items():
        if schema_name.strip().lower() == normalized_object_class or not isinstance(other_schema, dict):
            continue
        other_attributes = other_schema.get("attributes", [])
        if not isinstance(other_attributes, list):
            continue
        for attr in other_attributes:
            if not isinstance(attr, dict):
                continue
            attr_name = attr.get("name")
            if isinstance(attr_name, str) and attr_name.strip():
                other_standard_attributes.add(attr_name.strip().lower())

    return {
        "current_attributes": current_attributes,
        "complex_attributes": complex_attributes,
        "other_standard_attributes": other_standard_attributes,
    }


def get_scim_complex_attribute_reference_type(
    scim_path: str,
    attribute_context: Dict[str, set[str]],
    object_class: str,
) -> Optional[str]:
    """
    Return embedded object-class name for a SCIM path rooted in a complex attribute.
    """
    root = _get_attribute_root(scim_path).strip()
    if not root or root.startswith("urn:"):
        return None
    if root.lower() not in attribute_context["complex_attributes"]:
        return None
    return build_embedded_object_class_name(object_class, root)


def scim_path_targets_filtered_attribute(scim_path: str, attribute_context: Dict[str, set[str]]) -> bool:
    """
    True when a documented mapping belongs to another SCIM object class/scope.
    """
    root = _get_attribute_root(scim_path).strip().lower()
    if not root or root.startswith("urn:"):
        return False

    if root in attribute_context["other_standard_attributes"] and root not in attribute_context["current_attributes"]:
        return True

    return False


def _find_embedded_source_attribute(
    schemas: Dict[str, Any],
    object_class: str,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    normalized_name = object_class.strip().lower()
    for parent_class, schema in schemas.items():
        if not isinstance(schema, dict):
            continue
        attributes = schema.get("attributes", [])
        if not isinstance(attributes, list):
            continue
        for attr in attributes:
            if not isinstance(attr, dict) or attr.get("type") != "complex":
                continue
            attr_name = attr.get("name")
            if not isinstance(attr_name, str) or not attr_name.strip():
                continue
            embedded_name = build_embedded_object_class_name(parent_class, attr_name)
            if embedded_name.strip().lower() == normalized_name:
                return attr_name, attr
    return None


def get_scim_schema_attributes_for_object_class(object_class: str) -> Optional[Dict[str, Dict[str, Any]]]:
    """
    Return deterministic SCIM attributes for a standard or embedded object class.

    None means the object class is not backed by the local SCIM base schemas and
    should be handled by the custom/documentation extraction path.
    """
    schemas = load_scim_base_schemas()

    schema = _get_schema_case_insensitive(schemas, object_class)
    if schema is not None:
        result: Dict[str, Dict[str, Any]] = {}
        attributes = schema.get("attributes", [])
        if not isinstance(attributes, list):
            return result
        for attr in attributes:
            if not isinstance(attr, dict):
                continue
            attr_name = attr.get("name")
            if not isinstance(attr_name, str) or not attr_name.strip():
                continue
            if attr.get("type") == "complex":
                result[attr_name] = map_scim_attribute_to_digester_attribute(
                    attr,
                    attr_name,
                    attribute_type=build_embedded_object_class_name(object_class, attr_name),
                    attribute_format="embedded",
                )
                continue
            result[attr_name] = map_scim_attribute_to_digester_attribute(attr, attr_name)
        return result

    embedded_source = _find_embedded_source_attribute(schemas, object_class)
    if embedded_source is None:
        return None

    source_attr_name, source_attr = embedded_source
    result = {}
    sub_attributes = source_attr.get("subAttributes", [])
    if not isinstance(sub_attributes, list):
        return result
    for sub_attr in sub_attributes:
        if not isinstance(sub_attr, dict):
            continue
        sub_attr_name = sub_attr.get("name")
        if not isinstance(sub_attr_name, str) or not sub_attr_name.strip():
            continue
        scim_path = f"{source_attr_name}.{sub_attr_name}"
        result[sub_attr_name] = map_scim_attribute_to_digester_attribute(sub_attr, scim_path)
    return result
