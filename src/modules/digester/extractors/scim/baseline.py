# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
SCIM baseline schemas sourced from the session's uploaded connector schema documents.

The baseline SCIM 2.0 schemas (User, Group, EnterpriseUser, ...) are no longer bundled as
files. They are uploaded per session as midPoint connector-development documents under a
``conndev`` media type (one object class / one schema per document, kept whole — never
chunked). This module loads those documents from the DB and exposes the same digester-shaped
transformations the SCIM extractors rely on, taking the loaded ``schemas`` mapping explicitly
instead of reading a global cache.
"""

import json
import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from src.common.database.config import async_session_maker
from src.common.database.repositories.documentation_repository import DocumentationRepository
from src.common.documentation.content_types import is_conndev_content_type
from src.common.utils.coerce import as_dict_list, as_mapping

logger = logging.getLogger(__name__)


def _schema_name(schema: Dict[str, Any]) -> str:
    """Resolve a schema's object-class name from ``name`` or the trailing URN segment of ``id``."""
    name = schema.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    schema_id = schema.get("id")
    if isinstance(schema_id, str) and schema_id.strip():
        return schema_id.strip().rsplit(":", 1)[-1]
    return ""


async def load_session_scim_schemas(session_id: UUID) -> Dict[str, Any]:
    """
    Load the session's SCIM baseline schemas from its conndev connector documents.

    Returns a mapping of object-class name -> SCIM schema JSON, e.g.
    ``{"User": {...}, "Group": {...}}``. Documents that are not conndev, or whose content
    is not a valid SCIM schema object, are skipped (logged). Returns an empty mapping when the
    session has no conndev schema documents.
    """
    async with async_session_maker() as db:
        items = await DocumentationRepository(db).get_documentation_items_by_session(session_id)

    schemas: Dict[str, Any] = {}
    for item in items:
        metadata = as_mapping(item.get("metadata"))
        if not is_conndev_content_type(metadata.get("content_type")):
            continue

        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue

        try:
            schema = json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning(
                "[SCIM:Baseline] Skipping conndev document %s for session %s: invalid JSON (%s)",
                item.get("docId"),
                session_id,
                exc,
            )
            continue

        if not isinstance(schema, dict):
            continue

        name = _schema_name(schema)
        if not name:
            logger.warning(
                "[SCIM:Baseline] Skipping conndev document %s for session %s: schema has no resolvable name",
                item.get("docId"),
                session_id,
            )
            continue

        schemas[name] = schema

    logger.info("[SCIM:Baseline] Loaded %d baseline schema(s) for session %s", len(schemas), session_id)
    return schemas


def get_scim_schema(schemas: Dict[str, Any], class_name: str) -> Optional[Dict[str, Any]]:
    """Return a baseline schema by class name (case-insensitive), or None."""
    target = class_name.strip().lower()
    for name, schema in schemas.items():
        if name.strip().lower() == target and isinstance(schema, dict):
            return schema
    return None


def is_scim_standard_class(schemas: Dict[str, Any], class_name: str) -> bool:
    """True when ``class_name`` is one of the session's baseline schemas (case-insensitive)."""
    return get_scim_schema(schemas, class_name) is not None


def is_scim_extension_schema(schemas: Dict[str, Any], class_name: str) -> bool:
    """
    True when ``class_name`` maps to a SCIM *extension* schema rather than a standalone resource.

    Extension schemas (e.g. EnterpriseUser, whose URN is
    ``urn:ietf:params:scim:schemas:extension:enterprise:2.0:User``) augment another resource and are
    not exposed under their own endpoint, so no CRUD endpoints should be generated for them.
    Detection is based on the schema URN (``id``) rather than a hardcoded class name, so it applies to
    any extension schema the session provides.
    """
    schema = get_scim_schema(schemas, class_name)
    if not schema:
        return False
    schema_id = schema.get("id")
    return isinstance(schema_id, str) and ":extension:" in schema_id.lower()


def get_base_scim_object_classes(schemas: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the baseline schemas as digester object-class definitions."""
    object_classes: List[Dict[str, Any]] = []

    for class_name, schema in schemas.items():
        if not isinstance(schema, dict):
            continue
        obj_class = {
            "name": class_name,
            "schemaUrn": schema.get("id", ""),
            "relevant": "true",
            "confidence": "high",
            "superclass": None,
            "abstract": False,
            "embedded": False,
            "description": schema.get("description", f"SCIM 2.0 {class_name} resource"),
        }
        # EnterpriseUser extends User.
        if class_name == "EnterpriseUser":
            obj_class["superclass"] = "User"

        object_classes.append(obj_class)

    return object_classes


def get_base_scim_attributes(schemas: Dict[str, Any], class_name: str) -> Dict[str, Dict[str, Any]]:
    """Return the baseline attributes for ``class_name`` in digester AttributeInfo format."""
    schema = get_scim_schema(schemas, class_name)
    if not schema:
        logger.warning("[SCIM:Baseline] Schema not found for class: %s", class_name)
        return {}

    attributes: Dict[str, Dict[str, Any]] = {}
    for attr in as_dict_list(schema.get("attributes")):
        attr_name = attr.get("name")
        if not attr_name:
            continue

        mutability = attr.get("mutability", "readWrite")
        is_readonly = mutability in ("readOnly", "immutable")
        is_writeonly = mutability == "writeOnly"

        returned = attr.get("returned", "default")
        returned_by_default = returned != "request"

        attribute_info: Dict[str, Any] = {
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

        if attr.get("type") == "complex" and isinstance(attr.get("subAttributes"), list):
            attribute_info["subAttributes"] = {}
            for sub_attr in as_dict_list(attr.get("subAttributes")):
                sub_name = sub_attr.get("name")
                if sub_name:
                    attribute_info["subAttributes"][sub_name] = {
                        "type": _map_scim_type_to_digester(sub_attr.get("type")),
                        "description": sub_attr.get("description", ""),
                    }

        attributes[attr_name] = attribute_info

    logger.info("[SCIM:Baseline] Loaded %d attributes for %s", len(attributes), class_name)
    return attributes


def generate_scim_crud_endpoints(resource_path: str, class_name: str) -> List[Dict[str, Any]]:
    """Generate standard SCIM CRUD endpoints for a given resource path."""
    clean_path = (resource_path or "").strip()
    if not clean_path:
        clean_path = "/Resources"
    if not clean_path.startswith("/"):
        clean_path = "/" + clean_path
    clean_path = "/" + clean_path.strip("/")

    return [
        {
            "path": clean_path,
            "method": "GET",
            "description": f"Retrieve all {class_name}s with optional filtering, sorting, and pagination",
            "responseContentType": "application/scim+json",
            "requestContentType": None,
            "suggestedUse": ["getAll", "search"],
        },
        {
            "path": clean_path,
            "method": "POST",
            "description": f"Create a new {class_name} resource",
            "responseContentType": "application/scim+json",
            "requestContentType": "application/scim+json",
            "suggestedUse": ["create"],
        },
        {
            "path": f"{clean_path}/{{id}}",
            "method": "GET",
            "description": f"Retrieve a single {class_name} resource by ID",
            "responseContentType": "application/scim+json",
            "requestContentType": None,
            "suggestedUse": ["getById"],
        },
        {
            "path": f"{clean_path}/{{id}}",
            "method": "PUT",
            "description": f"Replace an existing {class_name} resource completely",
            "responseContentType": "application/scim+json",
            "requestContentType": "application/scim+json",
            "suggestedUse": ["update"],
        },
        {
            "path": f"{clean_path}/{{id}}",
            "method": "PATCH",
            "description": f"Modify an existing {class_name} resource partially using SCIM PATCH operations",
            "responseContentType": "application/scim+json",
            "requestContentType": "application/scim+json",
            "suggestedUse": ["update"],
        },
        {
            "path": f"{clean_path}/{{id}}",
            "method": "DELETE",
            "description": f"Delete an existing {class_name} resource",
            "responseContentType": None,
            "requestContentType": None,
            "suggestedUse": ["delete"],
        },
    ]


def _map_scim_type_to_digester(scim_type: Optional[str]) -> str:
    """Map a SCIM attribute type to the digester type format."""
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
    """Infer a digester format hint from a SCIM attribute definition."""
    scim_type = attr.get("type")

    if scim_type == "dateTime":
        return "date-time"
    if scim_type == "binary":
        return "binary"
    if scim_type == "reference":
        return "reference"
    if scim_type == "complex":
        return "embedded"

    name = str(attr.get("name", "")).lower()
    if "email" in name:
        return "email"
    if "url" in name or "uri" in name:
        return "uri"

    return None
