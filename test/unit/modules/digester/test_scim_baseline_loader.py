# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Regression tests for the conndev SCIM baseline loader.

A conndev upload delivers four document contracts. Three can share the same ``name`` value:
raw SCIM schemas (``schemaContent``), SCIM resources (``endpoint`` + ``primarySchema`` +
``schemaExtensions``), ConnId object classes (``locator`` + ``uid``), plus one session-wide
ServiceProviderConfig capability contract. The loader must keep them apart — mixing the
resource-facing contracts into one name-keyed mapping silently overwrote the raw schemas and
broke embedded-class and attribute derivation.
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.modules.digester.extractors.scim import baseline as scim_baseline
from src.modules.digester.extractors.scim.baseline import (
    ConnIdObjectClassDefinition,
    ScimResourceDefinition,
    build_scim_baseline_bundle,
    build_scim_codegen_context,
    get_base_scim_object_classes,
    get_scim_class_document_references,
    get_scim_resource_endpoint_definition,
    load_session_scim_baseline,
    map_scim_type_to_digester,
)
from src.modules.digester.extractors.scim.object_class import get_embedded_object_classes_from_scim_schemas

_MODULE = scim_baseline.__name__
_SCHEMA_DIR = Path(__file__).parent / "scim_schemas"
_CONNDEV_CONTENT_TYPE = "application/com.evolveum.conndev+json"

USER_SCHEMA = json.loads((_SCHEMA_DIR / "user.json").read_text())
GROUP_SCHEMA = json.loads((_SCHEMA_DIR / "group.json").read_text())
ENTERPRISE_USER_SCHEMA = json.loads((_SCHEMA_DIR / "enterpriseUser.json").read_text())


def _conndev_item(document: dict, url: str) -> dict:
    return {
        "docId": str(uuid4()),
        "chunkId": str(uuid4()),
        "url": url,
        "content": json.dumps(document),
        "metadata": {"content_type": _CONNDEV_CONTENT_TYPE},
    }


def _schema_document(raw_schema: dict) -> dict:
    """conndev ScimSchema contract: the raw SCIM schema serialized into ``schemaContent``."""
    return {"schemaContent": json.dumps(raw_schema), "name": raw_schema["name"], "id": raw_schema["id"]}


def _resource_document(raw_schema: dict, endpoint: str, extensions: list[dict]) -> dict:
    """conndev ScimResource contract: endpoint-bearing binding with embedded schema copies."""
    return {
        "schema": raw_schema["id"],
        "primarySchema": json.dumps(raw_schema),
        "endpoint": endpoint,
        "name": raw_schema["name"],
        "schemaExtensions": json.dumps(extensions),
        "id": raw_schema["name"],
    }


def _connid_document(name: str, namespace: str, locator: str, attributes: list[dict]) -> dict:
    """conndev ObjectClass contract: the ConnId view exposed by connector-scimrest."""
    return {"namespace": namespace, "attributes": attributes, "locator": locator, "name": name, "uid": name}


def _shadow_connid_document(name: str, schema_urn: str, attributes: list[dict]) -> dict:
    """Newer conndev ObjectClass contract: the same export wrapped in midPoint shadows."""

    def _attribute_shadow(attribute: dict) -> dict:
        connid_flags = {key: value for key, value in attribute.items() if key not in ("name", "scimPath")}
        return {
            "type": "c:ShadowType",
            "object": {
                "objectClass": "ri:conndev_Attribute",
                "exists": True,
                "attributes": {
                    "scim": {"path": attribute.get("scimPath", attribute["name"])},
                    "connId": connid_flags,
                    "name": attribute["name"],
                },
            },
        }

    return {
        "attributes": [_attribute_shadow(attribute) for attribute in attributes],
        "scim": {
            "type": "c:ShadowType",
            "object": {
                "objectClass": "ri:conndev_scim",
                "exists": True,
                "attributes": {"schemaUri": schema_urn, "name": name},
            },
        },
        "name": name,
        "uid": name,
    }


def _service_provider_document(
    *,
    patch_supported: bool = True,
    filter_max_results: int = 50,
) -> dict:
    return {
        "name": "ServiceProviderConfig",
        "content": json.dumps(
            {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
                "documentationUri": "https://scim.example.test",
                "patch": {"supported": patch_supported},
                "bulk": {
                    "supported": True,
                    "maxOperations": 15,
                    "maxPayloadSize": 2097152,
                },
                "filter": {
                    "supported": True,
                    "maxResults": filter_max_results,
                },
                "changePassword": {"supported": False},
                "sort": {"supported": True},
                "etag": {"supported": False},
                "authenticationSchemes": [
                    {
                        "name": "OAuth Bearer Token",
                        "description": "OAuth bearer token",
                        "specUri": "https://www.rfc-editor.org/info/rfc6750",
                        "type": "oauthbearertoken",
                        "primary": True,
                    }
                ],
                "meta": {
                    "resourceType": "ServiceProviderConfig",
                    "location": "https://scim.example.test/v2/ServiceProviderConfig",
                },
            }
        ),
        "id": "ServiceProviderConfig",
    }


def _session_conndev_items() -> list[dict]:
    """Example export: schemas, resources, ConnId classes and service-provider capabilities."""
    return [
        _conndev_item(_schema_document(USER_SCHEMA), "upload://conndev_ScimSchema_User.json"),
        _conndev_item(_schema_document(GROUP_SCHEMA), "upload://conndev_ScimSchema_Group.json"),
        _conndev_item(_schema_document(ENTERPRISE_USER_SCHEMA), "upload://conndev_ScimSchema_EnterpriseUser.json"),
        _conndev_item(
            _resource_document(USER_SCHEMA, "/Users", [ENTERPRISE_USER_SCHEMA]),
            "upload://conndev_ScimResource_User.json",
        ),
        _conndev_item(_resource_document(GROUP_SCHEMA, "/Groups", []), "upload://conndev_ScimResource_Group.json"),
        _conndev_item(
            _connid_document(
                "User",
                USER_SCHEMA["id"],
                "/Users",
                [{"name": "profileUrl", "type": "string"}, {"name": "active", "type": "boolean"}],
            ),
            "upload://conndev_ObjectClass_User.json",
        ),
        _conndev_item(
            _connid_document("Group", GROUP_SCHEMA["id"], "/Groups", [{"name": "displayName", "type": "string"}]),
            "upload://conndev_ObjectClass_Group.json",
        ),
        _conndev_item(
            _service_provider_document(),
            "upload://conndev_ScimServiceProviderConfig_ServiceProviderConfig.json",
        ),
    ]


class _NoopAsyncSession:
    """Async context manager standing in for ``async_session_maker()`` in unit tests."""

    async def __aenter__(self):
        return MagicMock()

    async def __aexit__(self, *exc):
        return False


async def _load_bundle(items: list[dict]):
    repo = MagicMock()
    repo.get_conndev_documentation_items_by_session = AsyncMock(return_value=items)

    with (
        patch(
            "src.modules.digester.extractors.scim.baseline.async_session_maker",
            return_value=_NoopAsyncSession(),
        ),
        patch("src.modules.digester.extractors.scim.baseline.DocumentationRepository", return_value=repo),
    ):
        return await load_session_scim_baseline(uuid4())


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse_order", [False, True])
async def test_loader_keeps_the_four_conndev_contracts_apart(reverse_order):
    """Schema/resource/ConnId/config documents must not overwrite each other, in any order."""
    items = _session_conndev_items()
    if reverse_order:
        items = list(reversed(items))

    bundle = await _load_bundle(items)

    assert set(bundle.schemas) == {"User", "Group", "EnterpriseUser"}
    # The registered schemas are the unwrapped raw SCIM schemas, not the wrapper or ConnId docs.
    assert bundle.schemas["User"] == USER_SCHEMA
    assert bundle.schemas["Group"] == GROUP_SCHEMA
    assert bundle.schemas["EnterpriseUser"] == ENTERPRISE_USER_SCHEMA

    assert set(bundle.resources) == {"User", "Group"}
    assert bundle.resources["User"].endpoint == "/Users"
    assert bundle.resources["Group"].endpoint == "/Groups"
    assert [ext["name"] for ext in bundle.resources["User"].extension_schemas] == ["EnterpriseUser"]

    assert set(bundle.connid_classes) == {"User", "Group"}
    assert bundle.connid_classes["User"].locator == "/Users"
    assert [attr["name"] for attr in bundle.connid_classes["User"].attributes] == ["profileUrl", "active"]

    assert bundle.extension_superclasses == {"EnterpriseUser": "User"}
    assert bundle.service_provider_config is not None
    assert bundle.service_provider_config.config.patch.supported is True
    assert bundle.service_provider_config.config.filter.max_results == 50
    assert bundle.service_provider_config.config.bulk.max_operations == 15


@pytest.mark.asyncio
async def test_loader_supports_arbitrary_schema_resource_and_object_class_names():
    device_schema: dict[str, Any] = {
        "id": "urn:example:params:scim:schemas:core:2.0:Device",
        "name": "Device",
        "attributes": [
            {"name": "serialNumber", "type": "string", "required": True},
            {"name": "owner", "type": "complex", "subAttributes": [{"name": "value", "type": "string"}]},
        ],
    }
    items = [
        _conndev_item(_schema_document(device_schema), "upload://conndev_ScimSchema_Device.json"),
        _conndev_item(
            _resource_document(device_schema, "/inventory/devices", []),
            "upload://conndev_ScimResource_Device.json",
        ),
        _conndev_item(
            _connid_document(
                "Device",
                device_schema["id"],
                "/inventory/devices",
                [
                    {"name": "serialNumber", "type": "string", "required": True},
                    {"name": "id", "type": "string", "updateable": False},
                ],
            ),
            "upload://conndev_ObjectClass_Device.json",
        ),
    ]

    bundle = await _load_bundle(items)

    assert set(bundle.schemas) == {"Device"}
    assert set(bundle.resources) == {"Device"}
    assert set(bundle.connid_classes) == {"Device"}
    endpoint_definition = get_scim_resource_endpoint_definition(bundle, "device")
    assert endpoint_definition is not None
    assert endpoint_definition.endpoint == "/inventory/devices"

    context = build_scim_codegen_context(bundle, "device")
    assert context["resource"]["endpoint"] == "/inventory/devices"
    assert context["resource"]["primarySchema"] == context["schema"]
    assert [attribute["name"] for attribute in context["schema"]["attributes"]] == ["serialNumber", "owner"]
    assert [attribute["name"] for attribute in context["connectorObjectClass"]["attributes"]] == [
        "serialNumber",
        "id",
    ]
    assert context["connectorObjectClass"]["attributes"][0]["mandatory"] is True
    assert context["connectorObjectClass"]["attributes"][1]["updatable"] is False
    assert len(get_scim_class_document_references(bundle, "device")) == 3


def test_codegen_context_preserves_differences_between_schema_resource_and_connid_views():
    standalone_schema: dict[str, Any] = {
        "id": "urn:example:Device",
        "name": "Device",
        "attributes": [{"name": "serialNumber", "type": "string", "required": True}],
    }
    resource_schema: dict[str, Any] = {
        "id": "urn:example:Device",
        "name": "Device",
        "attributes": [{"name": "serialNumber", "type": "integer", "required": False}],
    }
    resource = ScimResourceDefinition(
        name="Device",
        endpoint="/Devices",
        schema_urn=standalone_schema["id"],
        primary_schema=resource_schema,
    )
    connid_class = ConnIdObjectClassDefinition(
        name="Device",
        namespace=standalone_schema["id"],
        locator="/Devices",
        uid="Device",
        attributes=[{"name": "serialNumber", "type": "boolean"}],
    )
    bundle = build_scim_baseline_bundle(
        {"Device": standalone_schema},
        {"Device": resource},
        {"Device": connid_class},
    )

    context = build_scim_codegen_context(bundle, "Device")

    assert context["schema"]["attributes"][0]["type"] == "string"
    assert context["resource"]["primarySchema"]["attributes"][0]["type"] == "integer"
    assert context["connectorObjectClass"]["attributes"][0]["type"] == "boolean"
    assert "mandatory" not in context["connectorObjectClass"]["attributes"][0]


@pytest.mark.parametrize(
    ("exported_type", "expected_type"),
    [
        ("String", "string"),
        ("Boolean", "boolean"),
        ("INTEGER", "integer"),
        ("DateTime", "string"),
        ("Complex", "object"),
    ],
)
def test_scim_type_mapping_is_case_insensitive(exported_type, expected_type):
    assert map_scim_type_to_digester(exported_type) == expected_type


def test_unknown_scim_type_defaults_to_string_with_debug_log(caplog):
    with caplog.at_level("DEBUG", logger="src.modules.digester.extractors.scim.baseline"):
        assert map_scim_type_to_digester("CustomScalar") == "string"

    assert "Unknown attribute type 'CustomScalar'" in caplog.text


@pytest.mark.asyncio
async def test_loader_uses_latest_dedicated_schema_upload():
    older_schema = {**USER_SCHEMA, "description": "older"}
    latest_schema = {**USER_SCHEMA, "description": "latest"}

    bundle = await _load_bundle(
        [
            _conndev_item(_schema_document(older_schema), "upload://older-user.json"),
            _conndev_item(_schema_document(latest_schema), "upload://latest-user.json"),
        ]
    )

    assert bundle.schemas["User"]["description"] == "latest"


@pytest.mark.asyncio
async def test_loaded_baseline_supports_base_and_embedded_class_derivation():
    """The loaded bundle must yield the base classes and the embedded classes of the raw schemas."""
    bundle = await _load_bundle(_session_conndev_items())

    base_classes = {cls["name"]: cls for cls in get_base_scim_object_classes(bundle)}
    assert set(base_classes) == {"User", "Group", "EnterpriseUser"}
    assert base_classes["EnterpriseUser"]["embedded"] is True
    assert base_classes["EnterpriseUser"]["superclass"] is None
    assert base_classes["User"]["embedded"] is False
    assert base_classes["User"]["superclass"] is None

    embedded_names = {cls["name"] for cls in get_embedded_object_classes_from_scim_schemas(bundle.schemas)}
    assert {"UserName", "UserEmails", "UserGroups", "GroupMembers", "EnterpriseUserManager"} <= embedded_names

    user_refs = get_scim_class_document_references(bundle, "User")
    extension_refs = get_scim_class_document_references(bundle, "EnterpriseUser")
    assert len(user_refs) == 4
    assert len(extension_refs) == 2

    user_context = build_scim_codegen_context(bundle, "User")
    assert user_context["resource"]["endpoint"] == "/Users"
    assert [extension["name"] for extension in user_context["extensions"]] == ["EnterpriseUser"]
    assert [attribute["name"] for attribute in user_context["connectorObjectClass"]["attributes"]] == [
        "profileUrl",
        "active",
    ]
    assert user_context["serviceProviderConfig"]["filter"]["maxResults"] == 50
    assert user_context["serviceProviderConfig"]["authenticationSchemes"][0]["type"] == "oauthbearertoken"

    extension_context = build_scim_codegen_context(bundle, "EnterpriseUser")
    assert extension_context["extensionOf"] == "User"
    assert extension_context["schema"]["name"] == "EnterpriseUser"


@pytest.mark.asyncio
async def test_loader_falls_back_to_resource_embedded_schemas():
    """Sessions uploading only resource documents still get schemas from primarySchema/extensions."""
    items = [
        _conndev_item(
            _resource_document(USER_SCHEMA, "/Users", [ENTERPRISE_USER_SCHEMA]),
            "upload://conndev_ScimResource_User.json",
        ),
        _conndev_item(_resource_document(GROUP_SCHEMA, "/Groups", []), "upload://conndev_ScimResource_Group.json"),
    ]

    bundle = await _load_bundle(items)

    assert set(bundle.schemas) == {"User", "Group", "EnterpriseUser"}
    assert bundle.extension_superclasses == {"EnterpriseUser": "User"}


@pytest.mark.asyncio
async def test_loader_uses_latest_service_provider_config_upload():
    bundle = await _load_bundle(
        [
            _conndev_item(
                _service_provider_document(patch_supported=False, filter_max_results=25),
                "upload://older-service-provider-config.json",
            ),
            _conndev_item(
                _service_provider_document(patch_supported=True, filter_max_results=100),
                "upload://latest-service-provider-config.json",
            ),
        ]
    )

    assert bundle.service_provider_config is not None
    assert bundle.service_provider_config.config.patch.supported is True
    assert bundle.service_provider_config.config.filter.max_results == 100


@pytest.mark.asyncio
async def test_loader_rejects_invalid_service_provider_config_contract(caplog):
    invalid_document = _service_provider_document()
    raw_config = json.loads(invalid_document["content"])
    raw_config.pop("patch")
    invalid_document["content"] = json.dumps(raw_config)

    with caplog.at_level("WARNING", logger=_MODULE):
        bundle = await _load_bundle(
            [
                _conndev_item(
                    invalid_document,
                    "upload://invalid-service-provider-config.json",
                )
            ]
        )

    assert bundle.service_provider_config is None
    assert "invalid contract" in caplog.text


@pytest.mark.asyncio
async def test_loader_parses_shadow_wrapped_connid_object_class():
    """Newer conndev exports deliver the ConnId object class contract wrapped in midPoint shadows."""
    entitlement_urn = "urn:ietf:params:scim:schemas:custom:2.0:Entitlement"
    items = [
        _conndev_item(
            _shadow_connid_document(
                "Entitlement",
                entitlement_urn,
                [
                    {"name": "displayName", "type": "string", "creatable": False, "updateable": False},
                    {"name": "id", "type": "string", "creatable": False, "updateable": False, "required": True},
                    {"name": "type", "type": "string", "creatable": False, "updateable": False},
                ],
            ),
            "upload://conndev_ObjectClass_Entitlement.json",
        ),
    ]

    bundle = await _load_bundle(items)

    assert set(bundle.connid_classes) == {"Entitlement"}
    definition = bundle.connid_classes["Entitlement"]
    assert definition.namespace == entitlement_urn
    assert definition.uid == "Entitlement"
    assert [attribute["name"] for attribute in definition.attributes] == ["displayName", "id", "type"]

    context = build_scim_codegen_context(bundle, "entitlement")
    projected = context["connectorObjectClass"]["attributes"]
    assert [attribute["name"] for attribute in projected] == ["displayName", "id", "type"]
    assert projected[0]["creatable"] is False
    assert projected[0]["updatable"] is False
    assert projected[0]["scimAttribute"] == "displayName"
    assert projected[1]["mandatory"] is True


@pytest.mark.asyncio
async def test_shadow_wrapped_connid_class_coexists_with_dedicated_schema_document():
    """The shadow-wrapped ConnId view must not overwrite the raw schema registered under the same name."""
    items = [
        _conndev_item(_schema_document(USER_SCHEMA), "upload://conndev_ScimSchema_User.json"),
        _conndev_item(
            _shadow_connid_document(
                "User",
                USER_SCHEMA["id"],
                [{"name": "manager", "scimPath": "manager.value", "type": "string", "updateable": True}],
            ),
            "upload://conndev_ObjectClass_User.json",
        ),
    ]

    bundle = await _load_bundle(items)

    assert bundle.schemas["User"] == USER_SCHEMA
    assert set(bundle.connid_classes) == {"User"}
    assert bundle.connid_classes["User"].attributes == [
        {"type": "string", "updateable": True, "scimPath": "manager.value", "name": "manager"}
    ]

    context = build_scim_codegen_context(bundle, "user")
    projected = context["connectorObjectClass"]["attributes"]
    assert projected[0]["scimAttribute"] == "manager.value"
    assert projected[0]["updatable"] is True
    assert len(get_scim_class_document_references(bundle, "User")) == 2


@pytest.mark.asyncio
async def test_loader_skips_unrecognized_and_non_conndev_documents():
    items = [
        _conndev_item(_schema_document(USER_SCHEMA), "upload://conndev_ScimSchema_User.json"),
        _conndev_item({"something": "else"}, "upload://conndev_unknown.json"),
        {
            "docId": str(uuid4()),
            "chunkId": str(uuid4()),
            "url": "upload://guide.md",
            "content": json.dumps({"schemaContent": "{}"}),
            "metadata": {"content_type": "text/markdown"},
        },
    ]

    bundle = await _load_bundle(items)

    assert set(bundle.schemas) == {"User"}
    assert bundle.resources == {}
    assert bundle.connid_classes == {}


@pytest.mark.asyncio
async def test_loader_skips_embedded_sub_class_exports_without_warning(caplog):
    """midPoint exports one sub-class per complex attribute; the SCIM schemas already cover them."""
    items = [
        _conndev_item(_schema_document(USER_SCHEMA), "upload://conndev_ScimSchema_User.json"),
        _conndev_item(
            {
                "uid": "User__name",
                "name": "User__name",
                "attributes": [
                    {"object": {"objectClass": "ri:conndev_Attribute", "attributes": {"scim": {"path": "givenName"}}}}
                ],
            },
            "upload://conndev_ObjectClass_User__name.json",
        ),
        _conndev_item(
            {"uid": "Entitlement__typeInfo", "name": "Entitlement__typeInfo"},
            "upload://conndev_ObjectClass_Entitlement__typeInfo.json",
        ),
    ]

    with caplog.at_level("WARNING", logger=_MODULE):
        bundle = await _load_bundle(items)

    assert set(bundle.schemas) == {"User"}
    assert bundle.connid_classes == {}
    assert "unrecognized contract" not in caplog.text


@pytest.mark.asyncio
async def test_loader_does_not_use_conndev_filename_as_content_type_fallback():
    item = _conndev_item(_schema_document(USER_SCHEMA), "upload://conndev_ScimSchema_User.json")
    item["metadata"]["content_type"] = "application/json"

    bundle = await _load_bundle([item])

    assert bundle.schemas == {}


@pytest.mark.asyncio
async def test_loader_uses_conndev_content_type_with_arbitrary_filename():
    item = _conndev_item(_schema_document(USER_SCHEMA), "upload://enterprise-user-definition.json")

    bundle = await _load_bundle([item])

    assert bundle.schemas["User"] == USER_SCHEMA


def test_get_scim_resource_endpoint_definition_prefers_resource_then_connid_locator():
    resource = ScimResourceDefinition(
        name="User",
        endpoint="/scim/v2/Users",
        schema_urn=USER_SCHEMA["id"],
        primary_schema=USER_SCHEMA,
    )
    connid_class = ConnIdObjectClassDefinition(name="User", namespace=USER_SCHEMA["id"], locator="/Users", uid="User")

    with_resource = build_scim_baseline_bundle({"User": USER_SCHEMA}, {"User": resource}, {"User": connid_class})
    resource_definition = get_scim_resource_endpoint_definition(with_resource, "user")
    assert resource_definition is not None
    assert resource_definition.endpoint == "/scim/v2/Users"

    locator_only = build_scim_baseline_bundle({"User": USER_SCHEMA}, {}, {"User": connid_class})
    locator_definition = get_scim_resource_endpoint_definition(locator_only, "user")
    assert locator_definition is not None
    assert locator_definition.endpoint == "/Users"

    schemas_only = build_scim_baseline_bundle({"User": USER_SCHEMA})
    assert get_scim_resource_endpoint_definition(schemas_only, "user") is None
