# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json

from src.modules.codegen.utils.prompt_records import (
    build_attribute_context_records,
    build_attribute_mapping_records,
    build_connid_attribute_mapping_records,
    build_scim_contract_prompt_vars,
    build_sql_attribute_mapping_records,
    extract_scim_context,
)
from src.modules.digester.schemas import AttributeInfoScim, AttributeInfoSql, AttributeResponse


def test_build_attribute_context_records_strips_relevant_documentation_refs():
    payload = AttributeResponse(
        attributes={
            "userName": AttributeInfoScim(
                type="string",
                description="Login",
                relevant_documentations=[{"docId": "doc-1", "chunkId": "chunk-1"}],
            )
        }
    )

    records = build_attribute_context_records(payload)

    assert len(records) == 1
    assert records[0]["name"] == "userName"
    assert records[0]["type"] == "string"
    assert records[0]["description"] == "Login"
    assert "relevantDocumentations" not in records[0]
    assert "relevant_documentations" not in records[0]


def test_build_attribute_context_records_accepts_nested_and_flat_mappings():
    nested_records = build_attribute_context_records(
        {
            "attributes": {
                "email": {
                    "type": "string",
                    "relevantDocumentations": [{"docId": "doc-1", "chunkId": "chunk-1"}],
                }
            }
        }
    )
    flat_records = build_attribute_context_records({"id": {"type": "integer"}})

    assert nested_records == [{"name": "email", "type": "string"}]
    assert flat_records == [{"name": "id", "type": "integer"}]


def test_typed_attribute_response_preserves_sql_binding_for_codegen():
    payload = AttributeResponse.model_validate(
        {
            "attributes": {
                "Username": {
                    "type": "string",
                    "table": "m_user",
                    "column": "nameorig",
                    "primaryKey": True,
                    "creatable": False,
                    "updatable": False,
                }
            }
        }
    )

    attribute = payload.attributes["Username"]
    assert isinstance(attribute, AttributeInfoSql)

    serialized = payload.model_dump(by_alias=True, mode="json")
    serialized_attribute = serialized["attributes"]["Username"]
    assert serialized_attribute["table"] == "m_user"
    assert serialized_attribute["column"] == "nameorig"
    assert serialized_attribute["primaryKey"] is True

    context_record = build_attribute_context_records(payload)[0]
    assert context_record["table"] == "m_user"
    assert context_record["column"] == "nameorig"
    assert context_record["primaryKey"] is True


def test_build_attribute_mapping_records_uses_prompt_shape_and_sorting():
    records = build_attribute_mapping_records(
        {
            "attributes": {
                "id": {"type": "integer"},
                "email": {
                    "name": "Email",
                    "type": "string",
                    "format": "email",
                    "description": "Primary email",
                    "mandatory": True,
                    "updatable": True,
                    "updateable": False,
                    "creatable": True,
                    "readable": False,
                    "multivalue": True,
                    "returnedByDefault": False,
                    "relevantDocumentations": [{"docId": "doc-1", "chunkId": "chunk-1"}],
                },
            }
        }
    )

    assert records == [
        {
            "name": "Email",
            "jsonType": "string",
            "openApiFormat": "email",
            "description": "Primary email",
            "mandatory": True,
            "updateable": True,
            "creatable": True,
            "readable": False,
            "multivalue": True,
            "returnedByDefault": False,
        },
        {
            "name": "id",
            "jsonType": "integer",
            "openApiFormat": "",
            "description": "",
            "mandatory": False,
            "updateable": False,
            "creatable": False,
            "readable": True,
            "multivalue": False,
            "returnedByDefault": True,
        },
    ]


def test_build_attribute_mapping_records_preserves_scim_mapping_fields_when_present():
    records = build_attribute_mapping_records(
        {
            "attributes": {
                "login": {
                    "type": "string",
                    "scimAttribute": "userName",
                    "connectorExposed": True,
                }
            }
        }
    )

    assert records[0]["scimAttribute"] == "userName"
    assert records[0]["connectorExposed"] is True


def test_build_sql_attribute_mapping_records_preserves_physical_binding():
    payload = {
        "attributes": {
            "Username": {
                "type": "string",
                "table": "m_user",
                "column": "nameorig",
                "primaryKey": True,
            }
        }
    }

    sql_record = build_sql_attribute_mapping_records(payload)[0]
    assert sql_record["table"] == "m_user"
    assert sql_record["column"] == "nameorig"
    assert sql_record["primaryKey"] is True

    protocol_neutral_record = build_attribute_mapping_records(payload)[0]
    assert "table" not in protocol_neutral_record
    assert "column" not in protocol_neutral_record
    assert "primaryKey" not in protocol_neutral_record


def test_connid_mapping_prefers_scim_connector_object_class_projection():
    payload = {
        "attributes": {
            "userName": {"type": "string", "mandatory": True},
            "emails": {"type": "UserEmails", "multivalue": True},
        },
        "scimContext": {
            "connectorObjectClass": {
                "name": "User",
                "attributes": [
                    {
                        "name": "userName",
                        "type": "string",
                        "mandatory": True,
                        "scimAttribute": "userName",
                        "connectorExposed": True,
                    },
                    {
                        "name": "id",
                        "type": "string",
                        "updatable": False,
                        "scimAttribute": "id",
                        "connectorExposed": True,
                    },
                ],
            }
        },
    }

    records = build_connid_attribute_mapping_records(payload)

    assert [record["name"] for record in records] == ["id", "userName"]
    assert all(record["connectorExposed"] is True for record in records)
    assert "emails" not in {record["name"] for record in records}
    assert extract_scim_context(payload) == payload["scimContext"]


def test_connid_mapping_uses_provider_native_name_for_matching_scim_attribute():
    payload = {
        "attributes": {
            "Username": {
                "type": "string",
                "description": "Slack login name",
                "mandatory": False,
                "creatable": None,
                "scimAttribute": "userName",
            }
        },
        "scimContext": {
            "connectorObjectClass": {
                "name": "User",
                "attributes": [
                    {
                        "name": "userName",
                        "type": "string",
                        "mandatory": True,
                        "creatable": True,
                        "scimAttribute": "userName",
                        "connectorExposed": True,
                    },
                    {
                        "name": "id",
                        "type": "string",
                        "updatable": False,
                        "scimAttribute": "id",
                        "connectorExposed": True,
                    },
                ],
            }
        },
    }

    records = build_connid_attribute_mapping_records(payload)
    username = next(record for record in records if record["name"] == "Username")

    assert [record["name"] for record in records] == ["id", "Username"]
    assert username["scimAttribute"] == "userName"
    assert username["connectorExposed"] is True
    assert username["description"] == "Slack login name"
    assert username["mandatory"] is False
    assert username["creatable"] is True


def test_connid_mapping_falls_back_when_scim_projection_is_missing():
    records = build_connid_attribute_mapping_records({"attributes": {"displayName": {"type": "string"}}})

    assert [record["name"] for record in records] == ["displayName"]


def test_typed_attribute_response_preserves_scim_context_for_codegen():
    scim_context = {
        "resource": {"endpoint": "/Users"},
        "connectorObjectClass": {
            "name": "User",
            "attributes": [{"name": "userName", "type": "string"}],
        },
    }
    payload = AttributeResponse(
        attributes={"userName": AttributeInfoScim(type="string")},
        scimContext=scim_context,
    )

    assert extract_scim_context(payload) == scim_context
    assert [record["name"] for record in build_connid_attribute_mapping_records(payload)] == ["userName"]


def test_build_scim_contract_prompt_vars_keeps_source_abstractions_separate():
    scim_context = {
        "className": "User",
        "schema": {
            "name": "User",
            "urn": "urn:ietf:params:scim:schemas:core:2.0:User",
            "attributes": [{"name": "userName", "required": True}],
        },
        "resource": {
            "name": "User",
            "schemaUrn": "urn:ietf:params:scim:schemas:core:2.0:User",
            "endpoint": "/Users",
        },
        "extensions": [
            {
                "name": "EnterpriseUser",
                "urn": "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User",
            }
        ],
        "connectorObjectClass": {
            "name": "User",
            "locator": "/Users",
            "uid": "id",
            "attributes": [{"name": "userName", "type": "string"}],
        },
        "serviceProviderConfig": {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
            "patch": {"supported": True},
        },
    }

    prompt_vars = build_scim_contract_prompt_vars(
        {"attributes": {"userName": {"type": "string"}}, "scimContext": scim_context}
    )

    assert json.loads(prompt_vars["scim_protocol_schema_json"]) == scim_context["schema"]
    assert json.loads(prompt_vars["scim_resource_contract_json"]) == {
        "resource": scim_context["resource"],
        "extensions": scim_context["extensions"],
    }
    assert json.loads(prompt_vars["connid_object_class_json"]) == scim_context["connectorObjectClass"]
    assert json.loads(prompt_vars["scim_service_provider_config_json"]) == scim_context["serviceProviderConfig"]


def test_endpoint_service_provider_config_overrides_attribute_snapshot_for_codegen():
    prompt_vars = build_scim_contract_prompt_vars(
        {
            "attributes": {"userName": {"type": "string"}},
            "scimContext": {
                "serviceProviderConfig": {
                    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
                    "patch": {"supported": True},
                }
            },
        },
        {
            "endpoints": [],
            "scimCapabilities": {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
                "patch": {"supported": False},
            },
        },
    )

    assert json.loads(prompt_vars["scim_service_provider_config_json"])["patch"]["supported"] is False


def test_build_scim_contract_prompt_vars_preserves_extension_relationship():
    prompt_vars = build_scim_contract_prompt_vars(
        {
            "attributes": {"employeeNumber": {"type": "string"}},
            "scimContext": {
                "schema": {"name": "EnterpriseUser"},
                "extensionOf": "User",
            },
        }
    )

    assert json.loads(prompt_vars["scim_protocol_schema_json"]) == {"name": "EnterpriseUser"}
    assert json.loads(prompt_vars["scim_resource_contract_json"]) == {"extensionOf": "User"}
    assert json.loads(prompt_vars["connid_object_class_json"]) == {}
