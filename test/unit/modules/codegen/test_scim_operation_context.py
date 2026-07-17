# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json

from langchain_core.prompts import ChatPromptTemplate

from src.modules.codegen.core.operations import CreateGenerator
from src.modules.codegen.prompts.scim.create_prompts import (
    get_scim_create_system_prompt,
    get_scim_create_user_prompt,
)
from src.modules.codegen.prompts.scim.delete_prompts import (
    get_scim_delete_system_prompt,
    get_scim_delete_user_prompt,
)
from src.modules.codegen.prompts.scim.search_prompts import (
    get_scim_search_all_system_prompt,
    get_scim_search_filter_system_prompt,
    get_scim_search_id_system_prompt,
    get_scim_search_user_prompt,
)
from src.modules.codegen.prompts.scim.update_prompts import (
    get_scim_update_system_prompt,
    get_scim_update_user_prompt,
)


def test_scim_crud_input_keeps_contract_views_and_endpoints_separate():
    schema = {
        "name": "User",
        "urn": "urn:ietf:params:scim:schemas:core:2.0:User",
        "attributes": [{"name": "userName", "type": "string"}],
    }
    resource = {
        "name": "User",
        "endpoint": "/scim/v2/Users",
        "primarySchema": {
            "name": "User",
            "urn": "urn:ietf:params:scim:schemas:core:2.0:User",
            "attributes": [{"name": "userName", "type": "reference"}],
        },
    }
    extensions = [
        {
            "name": "EnterpriseUser",
            "urn": "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User",
            "attributes": [{"name": "employeeNumber", "type": "string"}],
        }
    ]
    connid_object_class = {
        "name": "User",
        "locator": "/scim/v2/Users",
        "uid": "User",
        "attributes": [{"name": "userName", "type": "boolean"}],
    }
    attributes = {
        "attributes": {"userName": {"type": "string"}},
        "scimContext": {
            "schema": schema,
            "resource": resource,
            "extensions": extensions,
            "connectorObjectClass": connid_object_class,
        },
    }
    endpoints = {
        "scimCapabilities": {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
            "patch": {"supported": False},
            "bulk": {"supported": True, "maxOperations": 15, "maxPayloadSize": 2097152},
            "filter": {"supported": True, "maxResults": 50},
            "changePassword": {"supported": False},
            "sort": {"supported": True},
            "etag": {"supported": False},
            "authenticationSchemes": [],
        },
        "endpoints": [
            {
                "method": "POST",
                "path": "/scim/v2/Users",
                "description": "Create User",
                "relevantDocumentations": [{"docId": "doc-1", "chunkId": "chunk-1"}],
            }
        ],
    }
    generator = CreateGenerator(
        object_class="User",
        docs_text="SCIM create docs",
        system_prompt=get_scim_create_system_prompt,
        user_prompt=get_scim_create_user_prompt,
        protocol_label="SCIM",
        include_scim_context=True,
    )

    prompt_vars = generator.prepare_input_data(attributes=attributes, endpoints=endpoints)

    assert json.loads(prompt_vars["scim_protocol_schema_json"]) == schema
    assert json.loads(prompt_vars["scim_resource_contract_json"]) == {
        "resource": resource,
        "extensions": extensions,
    }
    assert json.loads(prompt_vars["connid_object_class_json"]) == connid_object_class
    assert json.loads(prompt_vars["scim_service_provider_config_json"]) == endpoints["scimCapabilities"]
    assert json.loads(prompt_vars["endpoints_json"]) == [
        {"method": "POST", "path": "/scim/v2/Users", "description": "Create User"}
    ]


def test_protocol_neutral_crud_input_contains_no_scim_variables():
    generator = CreateGenerator(
        object_class="User",
        docs_text="REST create docs",
        system_prompt="REST system prompt",
        user_prompt="{attributes_json}{endpoints_json}",
        protocol_label="REST",
    )

    prompt_vars = generator.prepare_input_data(
        attributes={"attributes": {"userName": {"type": "string"}}},
        endpoints={"endpoints": [{"method": "POST", "path": "/users", "description": "Create user"}]},
    )

    assert set(prompt_vars) == {"attributes_json", "endpoints_json"}


def test_all_scim_operation_prompts_receive_the_separated_context_contract():
    system_prompts = [
        get_scim_create_system_prompt,
        get_scim_update_system_prompt,
        get_scim_delete_system_prompt,
        get_scim_search_all_system_prompt,
        get_scim_search_filter_system_prompt,
        get_scim_search_id_system_prompt,
    ]
    user_prompts = [
        get_scim_create_user_prompt,
        get_scim_update_user_prompt,
        get_scim_delete_user_prompt,
        get_scim_search_user_prompt,
    ]

    for system_prompt in system_prompts:
        assert "Keep the four supplied SCIM views separate" in system_prompt
        assert "Treat <extracted_endpoints> as deterministic" in system_prompt
        assert "<scim_service_provider_config>" in system_prompt

    for user_prompt in user_prompts:
        assert "{scim_protocol_schema_json}" in user_prompt
        assert "{scim_resource_contract_json}" in user_prompt
        assert "{connid_object_class_json}" in user_prompt
        assert "{scim_service_provider_config_json}" in user_prompt
        assert "{endpoints_json}" in user_prompt


def test_scim_filter_prompt_distinguishes_missing_and_explicitly_disabled_capability():
    prompt = " ".join(get_scim_search_filter_system_prompt.split())

    assert "explicit `filter.supported: false` forbids" in prompt
    assert "whole contract is empty" in prompt
    assert "treat filtering support as unknown" in prompt
    assert "provider documentation in <current_chunk> explicitly proves it" in prompt
    assert "An explicit `filter.supported: false` disables this intent" in prompt


def test_scim_operation_prompts_do_not_introduce_an_implicit_id_template_variable():
    prompt_pairs = [
        (get_scim_create_system_prompt, get_scim_create_user_prompt),
        (get_scim_update_system_prompt, get_scim_update_user_prompt),
        (get_scim_delete_system_prompt, get_scim_delete_user_prompt),
        (get_scim_search_all_system_prompt, get_scim_search_user_prompt),
        (get_scim_search_filter_system_prompt, get_scim_search_user_prompt),
        (get_scim_search_id_system_prompt, get_scim_search_user_prompt),
    ]

    for system_prompt, user_prompt in prompt_pairs:
        prompt = ChatPromptTemplate.from_messages([("system", system_prompt), ("human", user_prompt)])
        assert "id" not in prompt.input_variables
