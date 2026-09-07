# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
from collections.abc import Iterator
from typing import Any, Dict, List, Mapping, Sequence

from src.documents.normalize import normalize_scim_path_for_lookup
from src.modules.codegen.schema import AttributesPayload, EndpointsPayload
from src.modules.digester.schemas import AttributeResponse, EndpointResponse
from src.shared.coerce import as_dict_list, as_mapping

_ATTRIBUTE_MAPPING_OPTIONAL_FIELDS = ("scimAttribute", "connectorExposed")
_SQL_ATTRIBUTE_BINDING_FIELDS = (
    "databaseCatalog",
    "databaseSchema",
    "table",
    "column",
    "primaryKey",
    "foreignKey",
)


def _attribute_items(payload: AttributesPayload) -> Iterator[tuple[str, Any]]:
    if isinstance(payload, AttributeResponse):
        yield from (payload.attributes or {}).items()
        return

    if "attributes" in payload and isinstance(payload["attributes"], Mapping):
        yield from payload["attributes"].items()
        return

    yield from payload.items()


def _attribute_data(info: Any) -> Mapping[str, Any]:
    if hasattr(info, "model_dump"):
        return as_mapping(info.model_dump())

    return as_mapping(info)


def build_attribute_context_records(payload: AttributesPayload) -> List[Dict[str, Any]]:
    """
    Convert attributes into records for CRUD operation prompt context.

    Keeps the extracted attribute fields intact and only removes chunk
    provenance that is not useful inside the LLM prompt payload.
    """
    records: List[Dict[str, Any]] = []
    for name, info in _attribute_items(payload):
        item: Dict[str, Any] = {"name": name}
        item.update(strip_relevant_documentation_refs(_attribute_data(info)))
        records.append(item)
    return records


def _build_attribute_mapping_records(
    payload: AttributesPayload,
    *,
    optional_fields: tuple[str, ...],
) -> List[Dict[str, Any]]:
    """Build the shared attribute mapping shape with explicitly selected protocol fields."""
    records: List[Dict[str, Any]] = []
    for norm_key, info in _attribute_items(payload):
        data = _attribute_data(info)
        record = {
            "name": data.get("name") or norm_key,
            "jsonType": data.get("type") or "",
            "openApiFormat": data.get("format") or "",
            "description": data.get("description") or "",
            "mandatory": bool(data.get("mandatory", False)),
            "updateable": bool(data.get("updatable", data.get("updateable", False))),
            "creatable": bool(data.get("creatable", False)),
            "readable": bool(data.get("readable", True)),
            "multivalue": bool(data.get("multivalue", False)),
            "returnedByDefault": bool(data.get("returnedByDefault", True)),
        }
        for optional_key in optional_fields:
            if optional_key in data:
                record[optional_key] = data[optional_key]
        records.append(record)
    records.sort(key=lambda r: str(r.get("name", "")).lower())
    return records


def build_attribute_mapping_records(payload: AttributesPayload) -> List[Dict[str, Any]]:
    """
    Convert attributes into records for protocol-neutral mapping prompts.

    Note: Accepts either 'updatable' or legacy 'updateable' keys; 'updatable' wins.
    """
    return _build_attribute_mapping_records(
        payload,
        optional_fields=_ATTRIBUTE_MAPPING_OPTIONAL_FIELDS,
    )


def extract_scim_context(payload: AttributesPayload) -> Dict[str, Any]:
    """Return class-specific SCIM context persisted beside the extracted attributes."""
    if isinstance(payload, AttributeResponse):
        return dict(payload.scimContext)
    return dict(as_mapping(payload.get("scimContext")))


def build_scim_contract_prompt_vars(
    payload: AttributesPayload,
    endpoints: EndpointsPayload | None = None,
) -> Dict[str, str]:
    """Serialize the four SCIM source abstractions into separate prompt variables."""
    context = extract_scim_context(payload)
    protocol_schema = dict(as_mapping(context.get("schema")))

    resource_contract: Dict[str, Any] = {}
    resource = as_mapping(context.get("resource"))
    if resource:
        resource_contract["resource"] = dict(resource)
    extensions = context.get("extensions")
    if isinstance(extensions, list):
        resource_contract["extensions"] = extensions
    extension_of = context.get("extensionOf")
    if isinstance(extension_of, str) and extension_of.strip():
        resource_contract["extensionOf"] = extension_of.strip()

    connid_object_class = dict(as_mapping(context.get("connectorObjectClass")))
    service_provider_config = dict(as_mapping(context.get("serviceProviderConfig")))
    endpoint_capabilities = _extract_endpoint_scim_capabilities(endpoints)
    if endpoint_capabilities:
        service_provider_config = endpoint_capabilities

    def _serialize(value: Mapping[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    return {
        "scim_protocol_schema_json": _serialize(protocol_schema),
        "scim_resource_contract_json": _serialize(resource_contract),
        "connid_object_class_json": _serialize(connid_object_class),
        "scim_service_provider_config_json": _serialize(service_provider_config),
    }


def _extract_endpoint_scim_capabilities(payload: EndpointsPayload | None) -> Dict[str, Any]:
    if isinstance(payload, EndpointResponse):
        if payload.scim_capabilities is None:
            return {}
        return payload.scim_capabilities.model_dump(by_alias=True, exclude_none=True)
    if isinstance(payload, Mapping):
        return dict(as_mapping(payload.get("scimCapabilities")))
    return {}


def build_connid_attribute_mapping_records(payload: AttributesPayload) -> List[Dict[str, Any]]:
    """
    Build the effective native attributes available to ConnID mapping codegen.

    The SCIM connector ObjectClass projection controls framework exposure, while
    provider documentation controls the native connector name. Joining the two by
    ``scimAttribute`` preserves technical projection flags without replacing a
    provider-specific name such as ``Username`` with the protocol name
    ``userName``.

    Falling back to the normal attribute payload preserves behavior for REST/SQL
    sessions and SCIM resources without a connector ObjectClass document.
    """
    scim_context = extract_scim_context(payload)
    connector_object_class = as_mapping(scim_context.get("connectorObjectClass"))
    if not connector_object_class or not isinstance(connector_object_class.get("attributes"), list):
        return build_attribute_mapping_records(payload)

    provider_attributes_by_scim_path: Dict[str, tuple[str, Mapping[str, Any]]] = {}
    for attribute_name, attribute_info in _attribute_items(payload):
        data = _attribute_data(attribute_info)
        native_name = data.get("name") or attribute_name
        if not isinstance(native_name, str) or not native_name.strip():
            continue
        scim_path = normalize_scim_path_for_lookup(data.get("scimAttribute") or attribute_name)
        if scim_path:
            provider_attributes_by_scim_path.setdefault(scim_path, (native_name.strip(), data))

    projected_attributes: Dict[str, Dict[str, Any]] = {}
    for attribute in as_dict_list(connector_object_class.get("attributes")):
        name = attribute.get("name")
        if not isinstance(name, str) or not name.strip():
            continue

        projection_name = name.strip()
        scim_path = normalize_scim_path_for_lookup(attribute.get("scimAttribute") or projection_name)
        provider_attribute = provider_attributes_by_scim_path.get(scim_path)
        if provider_attribute is None:
            projected_attributes[projection_name] = attribute
            continue

        native_name, provider_data = provider_attribute
        effective_attribute = dict(attribute)
        effective_attribute.update(
            {
                key: value
                for key, value in provider_data.items()
                if value is not None and (not isinstance(value, str) or value.strip())
            }
        )
        effective_attribute["name"] = native_name
        effective_attribute["scimAttribute"] = (
            provider_data.get("scimAttribute") or attribute.get("scimAttribute") or projection_name
        )
        projected_attributes[native_name] = effective_attribute

    return build_attribute_mapping_records({"attributes": projected_attributes})


def build_complete_attribute_mapping_records(payload: AttributesPayload) -> List[Dict[str, Any]]:
    """
    Every extracted attribute, plus the identifiers only the ConnID projection knows.

    The record set for any prompt that reasons about a whole object class: native
    schema generation, which writes the attribute definitions and the ConnID mapping
    into one script, and the object-class fix. The projection-filtered ConnID record
    set is not enough on its own - it drops every attribute the connector does not
    already expose, and that is exactly where a wrong native name hides. The
    projection is merged in only where it can contribute something new - ``UID`` maps
    to ``id``, which the extracted attributes do not carry - which is the SCIM path,
    since :func:`build_connid_attribute_mapping_records` otherwise just rebuilds the
    records already collected here. Neither builder ever disagrees about a native
    name, so the merge order is not a policy.
    """
    records = _build_attribute_mapping_records(
        payload,
        optional_fields=_ATTRIBUTE_MAPPING_OPTIONAL_FIELDS + _SQL_ATTRIBUTE_BINDING_FIELDS,
    )
    if not as_mapping(extract_scim_context(payload).get("connectorObjectClass")):
        return records

    known = {record["name"] for record in records}
    records.extend(record for record in build_connid_attribute_mapping_records(payload) if record["name"] not in known)
    records.sort(key=lambda record: str(record.get("name", "")).lower())
    return records


def render_prompt_records(records: Sequence[Mapping[str, Any]]) -> str:
    """
    Serialize extracted records as an indented JSON block for a prompt.

    Deliberately indented, unlike the compact ``attributes_json`` / ``endpoints_json``
    the generators send: the fix prompt tells the model these records outrank the
    scripts under repair and the bundled DSL examples, so they are meant to be read
    rather than merely referenced. An empty set says so in words, because ``[]`` reads
    to a model as "this attribute has no properties" rather than "nothing was extracted".
    """
    if not records:
        return "No extracted records are available for this object class."
    return json.dumps(list(records), ensure_ascii=False, indent=2)


def strip_relevant_documentation_refs(record: Mapping[str, Any]) -> Dict[str, Any]:
    cleaned = dict(record)
    cleaned.pop("relevantDocumentations", None)
    cleaned.pop("relevant_documentations", None)
    return cleaned
