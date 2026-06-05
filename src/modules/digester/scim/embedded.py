# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
SCIM embedded object-class heuristics.

SCIM represents many structured values as complex attributes. For connector
schema purposes these complex values are useful as embedded object classes even
though they are not standalone SCIM resources.
"""

import re
from typing import Any, Dict, List


def _to_pascal_case(value: str) -> str:
    tokens = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", value)
    if not tokens:
        clean = re.sub(r"[^0-9A-Za-z]+", "", value)
        return clean[:1].upper() + clean[1:]
    return "".join(token[:1].upper() + token[1:] for token in tokens)


def build_embedded_object_class_name(parent_class_name: str, attribute_name: str) -> str:
    """
    Build a stable connector object-class name for a SCIM complex attribute.
    """
    parent = _to_pascal_case(parent_class_name)
    attribute = _to_pascal_case(attribute_name)
    return f"{parent}{attribute}"


def get_embedded_object_classes_from_scim_schema(
    class_name: str,
    schema: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Return embedded object-class definitions for complex SCIM attributes.
    """
    embedded_classes: List[Dict[str, Any]] = []
    seen_names: set[str] = set()

    attributes = schema.get("attributes", [])
    if not isinstance(attributes, list):
        return embedded_classes

    for attr in attributes:
        if not isinstance(attr, dict):
            continue
        if attr.get("type") != "complex":
            continue

        attr_name = attr.get("name")
        if not isinstance(attr_name, str) or not attr_name.strip():
            continue

        object_class_name = build_embedded_object_class_name(class_name, attr_name)
        normalized_name = object_class_name.strip().lower()
        if normalized_name in seen_names:
            continue
        seen_names.add(normalized_name)

        description = attr.get("description")
        if not isinstance(description, str) or not description.strip():
            description = f"Embedded SCIM complex attribute '{attr_name}' of {class_name}."

        embedded_classes.append(
            {
                "name": object_class_name,
                "superclass": None,
                "abstract": False,
                "embedded": True,
                "description": description.strip(),
                "sourceAttribute": attr_name,
            }
        )

    return embedded_classes


def get_embedded_object_classes_from_scim_schemas(schemas: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Return embedded object classes for all provided SCIM schemas.
    """
    embedded_classes: List[Dict[str, Any]] = []

    for class_name, schema in schemas.items():
        if not isinstance(schema, dict):
            continue
        embedded_classes.extend(get_embedded_object_classes_from_scim_schema(class_name, schema))

    return embedded_classes
