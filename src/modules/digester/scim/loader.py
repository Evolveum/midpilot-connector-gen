# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.


import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SCHEMA_CACHE: Dict[str, Any] = {}


def _get_scim_dir() -> Path:
    """Get the directory containing SCIM JSON schema files."""
    return Path(__file__).parent


def load_scim_base_schemas() -> Dict[str, Any]:
    """
    Load all SCIM 2.0 base schemas from JSON files.

    Returns:
        Dictionary mapping schema names to their JSON content:
        {
            "User": {...},
            "Group": {...},
            "EnterpriseUser": {...}
        }
    """
    if _SCHEMA_CACHE:
        return _SCHEMA_CACHE

    scim_dir = _get_scim_dir()

    try:
        schemas = {
            "User": json.loads((scim_dir / "user.json").read_text()),
            "Group": json.loads((scim_dir / "group.json").read_text()),
            "EnterpriseUser": json.loads((scim_dir / "enterpriseUser.json").read_text()),
        }

        _SCHEMA_CACHE.update(schemas)
        logger.info("[SCIM:Loader] Loaded %d base schemas", len(schemas))
        return schemas

    except FileNotFoundError as e:
        logger.error("[SCIM:Loader] Failed to load schema file: %s", e)
        raise
    except json.JSONDecodeError as e:
        logger.error("[SCIM:Loader] Failed to parse schema JSON: %s", e)
        raise


def get_scim_schema(class_name: str) -> Optional[Dict[str, Any]]:
    """
    Get a specific SCIM base schema by class name.

    Args:
        class_name: Name of the SCIM class (User, Group, EnterpriseUser)

    Returns:
        Schema dictionary or None if not found
    """
    schemas = load_scim_base_schemas()
    return schemas.get(class_name)


def get_base_scim_object_classes() -> List[Dict[str, Any]]:
    """
    Get SCIM 2.0 base object classes in digester ObjectClass format.

    Returns:
        List of object class definitions:
        [
            {
                "name": "User",
                "schemaUrn": "urn:ietf:params:scim:schemas:core:2.0:User",
                "relevant": "true",
                "description": "...",
                ...
            },
            ...
        ]
    """
    schemas = load_scim_base_schemas()

    object_classes = []

    for class_name, schema in schemas.items():
        obj_class = {
            "name": class_name,
            "schemaUrn": schema.get("id", ""),
            "relevant": "true",
            "superclass": None,
            "abstract": False,
            "embedded": False,
            "description": schema.get("description", f"SCIM 2.0 {class_name} resource"),
        }

        # EnterpriseUser extends User
        if class_name == "EnterpriseUser":
            obj_class["superclass"] = "User"

        object_classes.append(obj_class)

    return object_classes


def get_base_scim_attributes(class_name: str) -> Dict[str, Dict[str, Any]]:
    """
    Get SCIM 2.0 base attributes for a specific object class in digester AttributeInfo format.

    Args:
        class_name: Name of the SCIM class (User, Group, EnterpriseUser)

    Returns:
        Dictionary of attributes:
        {
            "userName": {
                "type": "string",
                "description": "...",
                "mandatory": True,
                "updatable": True,
                ...
            },
            ...
        }
    """
    schema = get_scim_schema(class_name)
    if not schema:
        logger.warning("[SCIM:Loader] Schema not found for class: %s", class_name)
        return {}

    attributes = {}
    scim_attributes = schema.get("attributes", [])

    for attr in scim_attributes:
        attr_name = attr.get("name")
        if not attr_name:
            continue

        # Map SCIM mutability to digester format
        mutability = attr.get("mutability", "readWrite")
        is_readonly = mutability in ["readOnly", "immutable"]
        is_writeonly = mutability == "writeOnly"

        # Map SCIM returned to digester format
        returned = attr.get("returned", "default")
        returned_by_default = returned != "request"

        attribute_info = {
            "type": _map_scim_type_to_digester(attr.get("type")),
            "format": _infer_format_from_scim_attr(attr),
            "description": attr.get("description", ""),
            "mandatory": attr.get("required", False),
            "updatable": not is_readonly,
            "creatable": not is_readonly,
            "readable": not is_writeonly,
            "multivalue": attr.get("multiValued", False),
            "returnedByDefault": returned_by_default,
        }

        # Handle complex types with subAttributes
        if attr.get("type") == "complex" and attr.get("subAttributes"):
            attribute_info["subAttributes"] = {}
            for sub_attr in attr["subAttributes"]:
                sub_name = sub_attr.get("name")
                if sub_name:
                    attribute_info["subAttributes"][sub_name] = {
                        "type": _map_scim_type_to_digester(sub_attr.get("type")),
                        "description": sub_attr.get("description", ""),
                    }

        attributes[attr_name] = attribute_info

    logger.info("[SCIM:Loader] Loaded %d attributes for %s", len(attributes), class_name)
    return attributes


def get_base_scim_endpoints(class_name: str, base_url: str = "") -> List[Dict[str, Any]]:
    """
    Get SCIM 2.0 base endpoints for a specific object class in digester EndpointInfo format.

    Args:
        class_name: Name of the SCIM class (User, Group)
        base_url: Base API URL (optional)

    Returns:
        List of endpoint definitions:
        [
            {
                "path": "/Users",
                "method": "GET",
                "description": "...",
                "responseContentType": "application/scim+json",
                "suggestedUse": ["getAll", "search"],
                ...
            },
            ...
        ]
    """
    # EnterpriseUser doesn't have its own endpoints (it's an extension of User)
    if class_name == "EnterpriseUser":
        return []

    # Map class name to SCIM resource path
    resource_path_map = {
        "User": "/Users",
        "Group": "/Groups",
    }

    resource_path = resource_path_map.get(class_name)
    if not resource_path:
        logger.warning("[SCIM:Loader] No standard endpoints for class: %s", class_name)
        return []

    endpoints = [
        {
            "path": resource_path,
            "method": "GET",
            "description": f"Retrieve all {class_name}s with optional filtering, sorting, and pagination",
            "responseContentType": "application/scim+json",
            "requestContentType": None,
            "suggestedUse": ["getAll", "search"],
        },
        {
            "path": resource_path,
            "method": "POST",
            "description": f"Create a new {class_name} resource",
            "responseContentType": "application/scim+json",
            "requestContentType": "application/scim+json",
            "suggestedUse": ["create"],
        },
        {
            "path": f"{resource_path}/{{id}}",
            "method": "GET",
            "description": f"Retrieve a single {class_name} resource by ID",
            "responseContentType": "application/scim+json",
            "requestContentType": None,
            "suggestedUse": ["getById"],
        },
        {
            "path": f"{resource_path}/{{id}}",
            "method": "PUT",
            "description": f"Replace an existing {class_name} resource completely",
            "responseContentType": "application/scim+json",
            "requestContentType": "application/scim+json",
            "suggestedUse": ["update"],
        },
        {
            "path": f"{resource_path}/{{id}}",
            "method": "PATCH",
            "description": f"Modify an existing {class_name} resource partially using SCIM PATCH operations",
            "responseContentType": "application/scim+json",
            "requestContentType": "application/scim+json",
            "suggestedUse": ["update"],
        },
        {
            "path": f"{resource_path}/{{id}}",
            "method": "DELETE",
            "description": f"Delete an existing {class_name} resource",
            "responseContentType": None,
            "requestContentType": None,
            "suggestedUse": ["delete"],
        },
    ]

    logger.info("[SCIM:Loader] Generated %d base endpoints for %s", len(endpoints), class_name)
    return endpoints  # type: ignore[return-value]


def get_scim_protocol_endpoints(base_url: str = "") -> List[Dict[str, Any]]:
    """
    Get SCIM 2.0 protocol endpoints (discovery and bulk operations).

    Args:
        base_url: Base API URL (optional)

    Returns:
        List of protocol endpoint definitions
    """
    endpoints = [
        {
            "path": "/ServiceProviderConfig",
            "method": "GET",
            "description": "Retrieve the service provider's configuration details (supported features, authentication schemes, bulk operations, etc.)",
            "responseContentType": "application/scim+json",
            "requestContentType": None,
            "suggestedUse": ["discovery"],
        },
        {
            "path": "/ResourceTypes",
            "method": "GET",
            "description": "Retrieve the types of resources available (User, Group, etc.)",
            "responseContentType": "application/scim+json",
            "requestContentType": None,
            "suggestedUse": ["discovery"],
        },
        {
            "path": "/Schemas",
            "method": "GET",
            "description": "Retrieve all schemas supported by the service provider",
            "responseContentType": "application/scim+json",
            "requestContentType": None,
            "suggestedUse": ["discovery"],
        },
        {
            "path": "/Schemas/{id}",
            "method": "GET",
            "description": "Retrieve a specific schema by URN",
            "responseContentType": "application/scim+json",
            "requestContentType": None,
            "suggestedUse": ["discovery"],
        },
        {
            "path": "/Bulk",
            "method": "POST",
            "description": "Perform multiple operations in a single request (bulk operations)",
            "responseContentType": "application/scim+json",
            "requestContentType": "application/scim+json",
            "suggestedUse": ["bulk"],
        },
    ]

    return endpoints  # type: ignore[return-value]


def format_scim_schema_for_prompt(class_name: str) -> str:
    """
    Format SCIM base schema for inclusion in LLM prompts.

    Args:
        class_name: Name of the SCIM class (User, Group, EnterpriseUser)

    Returns:
        Formatted string representation of the schema for LLM context
    """
    schema = get_scim_schema(class_name)
    if not schema:
        return f"[No base schema found for {class_name}]"

    lines = [
        f"SCIM 2.0 {class_name} Schema",
        f"URN: {schema.get('id', 'N/A')}",
        f"Description: {schema.get('description', 'N/A')}",
        "",
        "Standard Attributes:",
    ]

    attributes = schema.get("attributes", [])
    for attr in attributes[:20]:  # Limit to first 20 for prompt brevity
        attr_name = attr.get("name", "?")
        attr_type = attr.get("type", "?")
        required = " (REQUIRED)" if attr.get("required") else ""
        multivalue = " (multi-valued)" if attr.get("multiValued") else ""
        lines.append(f"  - {attr_name}: {attr_type}{required}{multivalue}")

    if len(attributes) > 20:
        lines.append(f"  ... and {len(attributes) - 20} more attributes")

    return "\n".join(lines)


def is_scim_standard_class(class_name: str) -> bool:
    """
    Check if the given class name is a SCIM 2.0 standard class.

    Args:
        class_name: Name of the object class

    Returns:
        True if it's a standard SCIM class (User, Group, EnterpriseUser)
    """
    normalized = class_name.strip().lower()
    return normalized in ["user", "group", "enterpriseuser"]


# Helper functions


def _map_scim_type_to_digester(scim_type: Optional[str]) -> str:
    """Map SCIM type to digester type format."""
    if not scim_type:
        return "string"

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

    return type_map.get(scim_type, "string")


def _infer_format_from_scim_attr(attr: Dict[str, Any]) -> Optional[str]:
    """Infer format from SCIM attribute definition."""
    scim_type = attr.get("type")

    if scim_type == "dateTime":
        return "date-time"
    elif scim_type == "binary":
        return "binary"
    elif scim_type == "reference":
        return "reference"
    elif scim_type == "complex":
        return "embedded"

    # Check for common patterns in attribute name
    name = attr.get("name", "").lower()
    if "email" in name:
        return "email"
    elif "url" in name or "uri" in name:
        return "uri"

    return None
