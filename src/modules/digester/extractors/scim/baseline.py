# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
SCIM baseline model sourced from the session's midPoint connector-development documents.

A conndev upload delivers three document contracts (one JSON document each, kept whole —
never chunked). The contract shapes are fixed by the midPoint/connector-scimrest export:

- **SCIM schema** — ``{"schemaContent": "<raw SCIM schema JSON>", "name", "id": <URN>}``.
  The canonical source of raw SCIM schemas: attributes, complex sub-attributes, mutability.
- **SCIM resource** — ``{"schema": <URN>, "primarySchema": "<raw schema JSON>", "endpoint",
  "name", "schemaExtensions": "<JSON list of raw schemas>", "id"}``. The resource model:
  which schemas are standalone resources, their real endpoints, and which extension schemas
  (e.g. EnterpriseUser) attach to which resource.
- **ConnId object class** — ``{"namespace": <URN>, "attributes", "locator", "name", "uid"}``.
  The final object class as exposed by connector-scimrest.

The three contracts share ``name`` values (User, Group, ...), so they must never be merged
into one mapping. This module classifies each document by its contract, unwraps the embedded
JSON payloads, and exposes them as a :class:`ScimBaselineBundle`. The digester-shaped schema
helpers keep taking the raw ``schemas`` mapping explicitly.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from uuid import UUID

from src.common.database.config import async_session_maker
from src.common.database.repositories.documentation_repository import DocumentationRepository
from src.common.documentation.content_types import is_conndev_content_type
from src.common.utils.coerce import as_dict_list, as_mapping
from src.modules.digester.schemas.common import ChunkReference

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScimResourceDefinition:
    """A SCIM resource as exported by connector-scimrest (endpoint-bearing schema binding)."""

    name: str
    endpoint: str
    schema_urn: str
    primary_schema: Dict[str, Any]
    extension_schemas: List[Dict[str, Any]] = field(default_factory=list)
    source_reference: Optional[ChunkReference] = None


@dataclass(frozen=True)
class ConnIdObjectClassDefinition:
    """The final ConnId object class view exposed by connector-scimrest."""

    name: str
    namespace: str
    locator: str
    uid: str
    attributes: List[Dict[str, Any]] = field(default_factory=list)
    source_reference: Optional[ChunkReference] = None


@dataclass(frozen=True)
class ScimBaselineBundle:
    """
    The session's SCIM baseline, split by document contract so same-named representations
    (SCIM schema vs. resource vs. ConnId class, all called "User") cannot overwrite each other.

    ``schemas`` maps canonical class name -> raw SCIM schema (the shape the attribute and
    embedded-class heuristics operate on). ``extension_superclasses`` maps an extension
    schema's class name (e.g. ``EnterpriseUser``) to the class it augments (e.g. ``User``).
    """

    schemas: Dict[str, Dict[str, Any]]
    resources: Dict[str, ScimResourceDefinition]
    connid_classes: Dict[str, ConnIdObjectClassDefinition]
    extension_superclasses: Dict[str, str]
    schema_references: Dict[str, ChunkReference] = field(default_factory=dict)


def _schema_name(schema: Dict[str, Any]) -> str:
    """Resolve a schema's object-class name from ``name`` or the trailing URN segment of ``id``."""
    name = schema.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    schema_id = schema.get("id")
    if isinstance(schema_id, str) and schema_id.strip():
        return schema_id.strip().rsplit(":", 1)[-1]
    return ""


def _parse_embedded_json(value: Any) -> Any:
    """Parse a conndev field that carries JSON as a string (already-parsed values pass through)."""
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return None


def _register_schema(
    schemas: Dict[str, Dict[str, Any]],
    schema_references: Dict[str, ChunkReference],
    raw_schema: Dict[str, Any],
    *,
    session_id: UUID,
    source: str,
    doc_id: Any,
    source_reference: Optional[ChunkReference] = None,
    replace_existing: bool = False,
) -> None:
    """
    Add a raw SCIM schema under its canonical name.

    Dedicated ``schemaContent`` documents replace older dedicated uploads, while resource-embedded
    copies (``primarySchema``/``schemaExtensions``) only fill classes that have no dedicated
    document. A same-named schema with a different URN is always logged.
    """
    name = _schema_name(raw_schema)
    if not name:
        logger.warning(
            "[SCIM:Baseline] Skipping %s schema from document %s for session %s: no resolvable name",
            source,
            doc_id,
            session_id,
        )
        return

    existing_key = _get_case_insensitive_key(schemas, name)
    existing = schemas.get(existing_key) if existing_key is not None else None
    if existing is not None:
        if not replace_existing:
            if existing.get("id") != raw_schema.get("id"):
                logger.warning(
                    "[SCIM:Baseline] Schema name conflict for '%s' in session %s: keeping URN %s, ignoring %s from %s",
                    name,
                    session_id,
                    existing.get("id"),
                    raw_schema.get("id"),
                    source,
                )
            return

        if existing.get("id") != raw_schema.get("id"):
            logger.warning(
                "[SCIM:Baseline] Replacing older schema '%s' in session %s: URN %s -> %s from %s",
                name,
                session_id,
                existing.get("id"),
                raw_schema.get("id"),
                source,
            )
        else:
            logger.info(
                "[SCIM:Baseline] Replacing older schema '%s' in session %s with document %s",
                name,
                session_id,
                doc_id,
            )
        if existing_key is not None:
            schemas.pop(existing_key, None)
            schema_references.pop(existing_key, None)

    schemas[name] = raw_schema
    if source_reference is not None:
        schema_references[name] = source_reference


def _get_case_insensitive_key(mapping: Dict[str, Any], key: str) -> Optional[str]:
    target = key.strip().lower()
    for name in mapping:
        if name.strip().lower() == target:
            return name
    return None


def _get_case_insensitive(mapping: Dict[str, Any], key: str) -> Optional[Any]:
    existing_key = _get_case_insensitive_key(mapping, key)
    return mapping.get(existing_key) if existing_key is not None else None


def _set_case_insensitive(mapping: Dict[str, Any], key: str, value: Any) -> None:
    existing_key = _get_case_insensitive_key(mapping, key)
    if existing_key is not None:
        mapping.pop(existing_key, None)
    mapping[key] = value


def _documentation_reference(item: Dict[str, Any]) -> Optional[ChunkReference]:
    doc_id = item.get("docId")
    chunk_id = item.get("chunkId")
    if not doc_id or not chunk_id:
        return None
    return ChunkReference(doc_id=str(doc_id), chunk_id=str(chunk_id))


def _parse_scim_resource(
    doc: Dict[str, Any],
    *,
    session_id: UUID,
    doc_id: Any,
    source_reference: Optional[ChunkReference] = None,
) -> Optional[ScimResourceDefinition]:
    primary_schema = _parse_embedded_json(doc.get("primarySchema"))
    if not isinstance(primary_schema, dict):
        logger.warning(
            "[SCIM:Baseline] Skipping resource document %s for session %s: invalid primarySchema",
            doc_id,
            session_id,
        )
        return None

    extensions_raw = _parse_embedded_json(doc.get("schemaExtensions"))
    extension_schemas = (
        [ext for ext in extensions_raw if isinstance(ext, dict)] if isinstance(extensions_raw, list) else []
    )

    raw_name = doc.get("name")
    name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else _schema_name(primary_schema)
    if not name:
        logger.warning(
            "[SCIM:Baseline] Skipping resource document %s for session %s: no resolvable name",
            doc_id,
            session_id,
        )
        return None

    endpoint = doc.get("endpoint")
    endpoint = endpoint.strip() if isinstance(endpoint, str) else ""
    if endpoint and not endpoint.startswith("/"):
        endpoint = f"/{endpoint}"

    schema_urn = doc.get("schema")
    return ScimResourceDefinition(
        name=name,
        endpoint=endpoint,
        schema_urn=schema_urn.strip() if isinstance(schema_urn, str) else "",
        primary_schema=primary_schema,
        extension_schemas=extension_schemas,
        source_reference=source_reference,
    )


def _parse_connid_object_class(
    doc: Dict[str, Any],
    *,
    session_id: UUID,
    doc_id: Any,
    source_reference: Optional[ChunkReference] = None,
) -> Optional[ConnIdObjectClassDefinition]:
    name = doc.get("name")
    if not isinstance(name, str) or not name.strip():
        logger.warning(
            "[SCIM:Baseline] Skipping ConnId object class document %s for session %s: no name",
            doc_id,
            session_id,
        )
        return None

    return ConnIdObjectClassDefinition(
        name=name.strip(),
        namespace=str(doc.get("namespace") or ""),
        locator=str(doc.get("locator") or ""),
        uid=str(doc.get("uid") or ""),
        attributes=as_dict_list(doc.get("attributes")),
        source_reference=source_reference,
    )


def build_scim_baseline_bundle(
    schemas: Dict[str, Dict[str, Any]],
    resources: Optional[Dict[str, ScimResourceDefinition]] = None,
    connid_classes: Optional[Dict[str, ConnIdObjectClassDefinition]] = None,
    schema_references: Optional[Dict[str, ChunkReference]] = None,
) -> ScimBaselineBundle:
    """
    Assemble a bundle and derive the extension -> base class mapping.

    The mapping comes primarily from the resources' ``schemaExtensions`` (EnterpriseUser is an
    extension of the User resource). For extension schemas no resource references (only the
    schema document was uploaded), the trailing URN segment is matched against the known
    schema names (``...:extension:enterprise:2.0:User`` -> ``User``).
    """
    resources = resources or {}
    connid_classes = connid_classes or {}

    extension_superclasses: Dict[str, str] = {}
    for resource in resources.values():
        base_name = _schema_name(resource.primary_schema) or resource.name
        for extension in resource.extension_schemas:
            extension_name = _schema_name(extension)
            if extension_name and base_name and extension_name.lower() != base_name.lower():
                extension_superclasses[extension_name] = base_name

    mapped_lower = {name.lower() for name in extension_superclasses}
    names_by_lower = {name.strip().lower(): name for name in schemas}
    for name, schema in schemas.items():
        if name.lower() in mapped_lower:
            continue
        urn = schema.get("id")
        if not isinstance(urn, str) or ":extension:" not in urn.lower():
            continue
        base = names_by_lower.get(urn.strip().rsplit(":", 1)[-1].lower())
        if base and base.lower() != name.lower():
            extension_superclasses[name] = base

    return ScimBaselineBundle(
        schemas=schemas,
        resources=resources,
        connid_classes=connid_classes,
        extension_superclasses=extension_superclasses,
        schema_references=schema_references or {},
    )


async def load_session_scim_baseline(session_id: UUID) -> ScimBaselineBundle:
    """
    Load and classify the session's conndev documents into the SCIM baseline bundle.

    Only persisted documents marked with a conndev metadata content type are considered. Those
    documents are then classified by contract shape (``schemaContent`` / ``endpoint`` +
    ``primarySchema`` / ``locator`` + ``uid``); unrecognized conndev documents are logged and
    skipped. Raw SCIM schemas come from the dedicated schema documents; resource-embedded copies
    only fill in classes that have no dedicated document.
    """
    async with async_session_maker() as db:
        items = await DocumentationRepository(db).get_documentation_items_by_session(session_id)

    schemas: Dict[str, Dict[str, Any]] = {}
    schema_references: Dict[str, ChunkReference] = {}
    resources: Dict[str, ScimResourceDefinition] = {}
    connid_classes: Dict[str, ConnIdObjectClassDefinition] = {}
    fallback_schemas: List[tuple[Dict[str, Any], str, Any, Optional[ChunkReference]]] = []

    for item in items:
        metadata = as_mapping(item.get("metadata"))
        if not is_conndev_content_type(metadata.get("content_type")):
            continue

        doc_id = item.get("docId")
        source_reference = _documentation_reference(item)
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue

        try:
            doc = json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning(
                "[SCIM:Baseline] Skipping conndev document %s for session %s: invalid JSON (%s)",
                doc_id,
                session_id,
                exc,
            )
            continue

        if not isinstance(doc, dict):
            continue

        if "schemaContent" in doc:
            raw_schema = _parse_embedded_json(doc.get("schemaContent"))
            if isinstance(raw_schema, dict):
                _register_schema(
                    schemas,
                    schema_references,
                    raw_schema,
                    session_id=session_id,
                    source="schemaContent",
                    doc_id=doc_id,
                    source_reference=source_reference,
                    replace_existing=True,
                )
            else:
                logger.warning(
                    "[SCIM:Baseline] Skipping schema document %s for session %s: invalid schemaContent",
                    doc_id,
                    session_id,
                )
        elif "endpoint" in doc and "primarySchema" in doc:
            resource = _parse_scim_resource(
                doc,
                session_id=session_id,
                doc_id=doc_id,
                source_reference=source_reference,
            )
            if resource is not None:
                _set_case_insensitive(resources, resource.name, resource)
                fallback_schemas.append((resource.primary_schema, "primarySchema", doc_id, source_reference))
                for extension in resource.extension_schemas:
                    fallback_schemas.append((extension, "schemaExtensions", doc_id, source_reference))
        elif "locator" in doc and "uid" in doc:
            connid_class = _parse_connid_object_class(
                doc,
                session_id=session_id,
                doc_id=doc_id,
                source_reference=source_reference,
            )
            if connid_class is not None:
                _set_case_insensitive(connid_classes, connid_class.name, connid_class)
        else:
            logger.warning(
                "[SCIM:Baseline] Skipping conndev document %s for session %s: unrecognized contract (keys: %s)",
                doc_id,
                session_id,
                sorted(doc.keys()),
            )

    for raw_schema, source, doc_id, source_reference in reversed(fallback_schemas):
        _register_schema(
            schemas,
            schema_references,
            raw_schema,
            session_id=session_id,
            source=source,
            doc_id=doc_id,
            source_reference=source_reference,
        )

    bundle = build_scim_baseline_bundle(schemas, resources, connid_classes, schema_references)
    logger.info(
        "[SCIM:Baseline] Session %s: loaded %d SCIM schema(s), %d resource(s), %d ConnId object class(es), "
        "%d extension mapping(s)",
        session_id,
        len(bundle.schemas),
        len(bundle.resources),
        len(bundle.connid_classes),
        len(bundle.extension_superclasses),
    )
    return bundle


def get_scim_schema(schemas: Dict[str, Any], class_name: str) -> Optional[Dict[str, Any]]:
    """Return a baseline schema by class name (case-insensitive), or None."""
    schema = _get_case_insensitive(schemas, class_name)
    return schema if isinstance(schema, dict) else None


def is_scim_standard_class(schemas: Dict[str, Any], class_name: str) -> bool:
    """True when ``class_name`` is one of the session's baseline schemas (case-insensitive)."""
    return get_scim_schema(schemas, class_name) is not None


def get_scim_canonical_class_name(schemas: Dict[str, Any], class_name: str) -> Optional[str]:
    """
    Return the schema-declared, canonically cased name for ``class_name``, or None when the class is
    not backed by a session schema.

    ``class_name`` reaches the extractors already lower-cased for case-insensitive matching, so this
    recovers the original casing (e.g. ``"user"`` -> ``"User"``) from the matching baseline schema.
    Callers use it to build SCIM resource paths and descriptions that follow the schema's casing
    (``/Users``) instead of the lower-cased request value (``/users``).
    """
    schema = get_scim_schema(schemas, class_name)
    if schema is None:
        return None
    return _schema_name(schema) or None


def is_scim_extension_schema(bundle: ScimBaselineBundle, class_name: str) -> bool:
    """
    True when ``class_name`` maps to a SCIM *extension* schema rather than a standalone resource.

    Extension schemas (e.g. EnterpriseUser) augment another resource and are not exposed under
    their own endpoint, so no CRUD endpoints should be generated for them. A class is an
    extension when a resource references it via ``schemaExtensions`` or its URN contains
    ``:extension:``.
    """
    normalized = class_name.strip().lower()
    if any(name.strip().lower() == normalized for name in bundle.extension_superclasses):
        return True
    schema = get_scim_schema(bundle.schemas, class_name)
    if not schema:
        return False
    schema_id = schema.get("id")
    return isinstance(schema_id, str) and ":extension:" in schema_id.lower()


def get_scim_resource_endpoint(bundle: ScimBaselineBundle, class_name: str) -> Optional[str]:
    """
    Return the real endpoint for ``class_name`` from the exported baseline, or None.

    The resource document's ``endpoint`` is authoritative; the ConnId object class ``locator``
    is the secondary source. Both come from the connector export, so they beat name-based
    path inference.
    """
    resource = _get_case_insensitive(bundle.resources, class_name)
    if isinstance(resource, ScimResourceDefinition) and resource.endpoint:
        return resource.endpoint

    connid_class = _get_case_insensitive(bundle.connid_classes, class_name)
    if isinstance(connid_class, ConnIdObjectClassDefinition) and connid_class.locator:
        locator = connid_class.locator.strip()
        return locator if locator.startswith("/") else f"/{locator}"

    return None


def _append_reference(
    references: List[Dict[str, str]],
    seen: set[tuple[str, str]],
    reference: Optional[ChunkReference],
) -> None:
    if reference is None:
        return
    key = (reference.doc_id, reference.chunk_id)
    if key in seen:
        return
    references.append(reference.to_internal_dict())
    seen.add(key)


def get_scim_class_document_references(
    bundle: ScimBaselineBundle,
    class_name: str,
    *,
    source_class_name: Optional[str] = None,
) -> List[Dict[str, str]]:
    """
    Return the conndev documents that deterministically define one SCIM class.

    A resource class references its standalone schema, resource descriptor, connector object
    class and attached extension schemas. An extension additionally references the parent
    resource that binds it. ``source_class_name`` lets derived embedded classes inherit the
    provenance of the schema attribute that defines them.
    """
    lookup_name = source_class_name or class_name
    references: List[Dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    schema_key = _get_case_insensitive_key(bundle.schemas, lookup_name)
    if schema_key is not None:
        _append_reference(references, seen, _get_case_insensitive(bundle.schema_references, schema_key))

    resource = _get_case_insensitive(bundle.resources, lookup_name)
    if isinstance(resource, ScimResourceDefinition):
        _append_reference(references, seen, resource.source_reference)
        for extension in resource.extension_schemas:
            extension_name = _schema_name(extension)
            if extension_name:
                _append_reference(
                    references,
                    seen,
                    _get_case_insensitive(bundle.schema_references, extension_name),
                )

    connid_class = _get_case_insensitive(bundle.connid_classes, lookup_name)
    if isinstance(connid_class, ConnIdObjectClassDefinition):
        _append_reference(references, seen, connid_class.source_reference)

    parent_name = _get_case_insensitive(bundle.extension_superclasses, lookup_name)
    if isinstance(parent_name, str):
        parent_resource = _get_case_insensitive(bundle.resources, parent_name)
        if isinstance(parent_resource, ScimResourceDefinition):
            _append_reference(references, seen, parent_resource.source_reference)

    return references


def get_base_scim_object_classes(bundle: ScimBaselineBundle) -> List[Dict[str, Any]]:
    """Return the baseline schemas as digester object-class definitions."""
    object_classes: List[Dict[str, Any]] = []

    for class_name, schema in bundle.schemas.items():
        if not isinstance(schema, dict):
            continue
        object_classes.append(
            {
                "name": class_name,
                "schemaUrn": schema.get("id", ""),
                "relevant": "true",
                "superclass": bundle.extension_superclasses.get(class_name),
                "abstract": False,
                "embedded": False,
                "description": schema.get("description", f"SCIM 2.0 {class_name} resource"),
            }
        )

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
        is_updatable = mutability not in ("readOnly", "immutable")
        is_creatable = mutability != "readOnly"
        is_readable = mutability != "writeOnly"

        returned = attr.get("returned", "default")
        returned_by_default = returned in ("always", "default")

        attribute_info: Dict[str, Any] = {
            "type": _map_scim_type_to_digester(attr.get("type")),
            "format": _infer_format_from_scim_attr(attr),
            "description": attr.get("description", ""),
            "mandatory": attr.get("required", False),
            "updatable": is_updatable,
            "creatable": is_creatable,
            "readable": is_readable,
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


_SCIM_ATTRIBUTE_CONTEXT_KEYS = (
    "name",
    "type",
    "multiValued",
    "required",
    "caseExact",
    "mutability",
    "returned",
    "uniqueness",
    "canonicalValues",
    "referenceTypes",
)


def _compact_scim_attribute(attribute: Dict[str, Any]) -> Dict[str, Any]:
    """Keep SCIM rules needed by codegen without duplicating verbose schema descriptions."""
    compact = {key: attribute[key] for key in _SCIM_ATTRIBUTE_CONTEXT_KEYS if key in attribute}
    sub_attributes = as_dict_list(attribute.get("subAttributes"))
    if sub_attributes:
        compact["subAttributes"] = [_compact_scim_attribute(sub_attribute) for sub_attribute in sub_attributes]
    return compact


def _schema_codegen_context(schema: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": _schema_name(schema),
        "urn": str(schema.get("id") or ""),
        "attributes": [_compact_scim_attribute(attribute) for attribute in as_dict_list(schema.get("attributes"))],
    }


def _find_schema_attribute(schema: Dict[str, Any], attribute_name: str) -> Optional[Dict[str, Any]]:
    normalized_name = attribute_name.strip().lower()
    for attribute in as_dict_list(schema.get("attributes")):
        name = attribute.get("name")
        if isinstance(name, str) and name.strip().lower() == normalized_name:
            return attribute
    return None


def _build_connector_attribute_projection(
    definition: ConnIdObjectClassDefinition,
    schema: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Map the simplified connector contract to the attribute shape used by codegen prompts."""
    projected: List[Dict[str, Any]] = []
    schema_attributes = get_base_scim_attributes({definition.name: schema}, definition.name) if schema else {}
    for raw_attribute in definition.attributes:
        name = raw_attribute.get("name")
        if not isinstance(name, str) or not name.strip():
            continue

        schema_attribute = _find_schema_attribute(schema, name) if schema is not None else None
        enriched = _get_case_insensitive(schema_attributes, name) if schema_attribute is not None else {}
        if not isinstance(enriched, dict):
            enriched = {}
        item: Dict[str, Any] = dict(enriched)
        item["name"] = name.strip()
        item["scimAttribute"] = name.strip()
        item["connectorExposed"] = True

        connector_type = raw_attribute.get("type")
        if connector_type:
            item["type"] = _map_scim_type_to_digester(connector_type)
        if "required" in raw_attribute:
            item["mandatory"] = bool(raw_attribute.get("required"))
        if "updateable" in raw_attribute or "updatable" in raw_attribute:
            item["updatable"] = bool(raw_attribute.get("updatable", raw_attribute.get("updateable")))
        if "creatable" in raw_attribute:
            item["creatable"] = bool(raw_attribute.get("creatable"))
        if "readable" in raw_attribute:
            item["readable"] = bool(raw_attribute.get("readable"))
        if "multivalue" in raw_attribute or "multiValued" in raw_attribute:
            item["multivalue"] = bool(raw_attribute.get("multivalue", raw_attribute.get("multiValued")))

        projected.append(item)

    return projected


def build_scim_codegen_context(bundle: ScimBaselineBundle, class_name: str) -> Dict[str, Any]:
    """
    Build bounded, class-specific SCIM context for native-schema and ConnId code generation.

    The context deliberately preserves the three source abstractions instead of flattening them:
    schema rules, resource endpoint/extension bindings and the connector's exposed projection.
    """
    schema = get_scim_schema(bundle.schemas, class_name)
    resource = _get_case_insensitive(bundle.resources, class_name)
    connid_class = _get_case_insensitive(bundle.connid_classes, class_name)
    parent_name = _get_case_insensitive(bundle.extension_superclasses, class_name)

    if (
        schema is None
        and not isinstance(resource, ScimResourceDefinition)
        and not isinstance(connid_class, ConnIdObjectClassDefinition)
    ):
        return {}

    context: Dict[str, Any] = {"className": _schema_name(schema) if schema else class_name}
    if schema is not None:
        context["schema"] = _schema_codegen_context(schema)

    if isinstance(resource, ScimResourceDefinition):
        context["resource"] = {
            "name": resource.name,
            "schemaUrn": resource.schema_urn,
            "endpoint": resource.endpoint,
        }
        extension_contexts: List[Dict[str, Any]] = []
        for embedded_extension in resource.extension_schemas:
            extension_name = _schema_name(embedded_extension)
            canonical_extension = get_scim_schema(bundle.schemas, extension_name) or embedded_extension
            extension_contexts.append(_schema_codegen_context(canonical_extension))
        context["extensions"] = extension_contexts

    if isinstance(parent_name, str):
        context["extensionOf"] = parent_name

    if isinstance(connid_class, ConnIdObjectClassDefinition):
        context["connectorObjectClass"] = {
            "name": connid_class.name,
            "namespace": connid_class.namespace,
            "locator": connid_class.locator,
            "uid": connid_class.uid,
            "attributes": _build_connector_attribute_projection(connid_class, schema),
        }

    return context


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
