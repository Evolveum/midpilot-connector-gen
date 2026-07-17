# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
from collections.abc import Iterator
from typing import Any, Dict, List, Mapping

from src.common.utils.coerce import as_dict_list, as_mapping
from src.modules.codegen.schema import AttributesPayload, EndpointsPayload
from src.modules.digester.schemas import AttributeResponse, EndpointResponse


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


def build_attribute_mapping_records(payload: AttributesPayload) -> List[Dict[str, Any]]:
    """
    Convert attributes into records for ConnID/native-schema mapping prompts.

    Note: Accepts either 'updatable' or legacy 'updateable' keys; 'updatable' wins.
    """
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
        for optional_key in ("scimAttribute", "connectorExposed"):
            if optional_key in data:
                record[optional_key] = data[optional_key]
        records.append(record)
    records.sort(key=lambda r: str(r.get("name", "")).lower())
    return records


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
    Use the connector ObjectClass projection for ConnId codegen when the SCIM export provides it.

    Falling back to the normal attribute payload preserves behavior for REST/SQL sessions and for
    SCIM resources without a connector ObjectClass document.
    """
    scim_context = extract_scim_context(payload)
    connector_object_class = as_mapping(scim_context.get("connectorObjectClass"))
    if not connector_object_class or not isinstance(connector_object_class.get("attributes"), list):
        return build_attribute_mapping_records(payload)

    projected_attributes: Dict[str, Dict[str, Any]] = {}
    for attribute in as_dict_list(connector_object_class.get("attributes")):
        name = attribute.get("name")
        if isinstance(name, str) and name.strip():
            projected_attributes[name.strip()] = attribute

    return build_attribute_mapping_records({"attributes": projected_attributes})


def strip_relevant_documentation_refs(record: Mapping[str, Any]) -> Dict[str, Any]:
    cleaned = dict(record)
    cleaned.pop("relevantDocumentations", None)
    cleaned.pop("relevant_documentations", None)
    return cleaned
