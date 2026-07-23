# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from langchain_core.prompts import ChatPromptTemplate

from src.modules.codegen.core.operations import (
    CreateGenerator,
    DeleteGenerator,
    SearchGenerator,
    UpdateGenerator,
)
from src.modules.codegen.enums import SearchIntent
from src.modules.codegen.prompts.cleanup_prompts import get_groovy_cleanup_system_prompt
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


def test_all_scim_crud_generators_enable_context_only_conndev_generation():
    shared_kwargs = {
        "object_class": "User",
        "docs_text": "SCIM operation docs",
        "system_prompt": "System prompt",
        "user_prompt": "{chunk}",
        "protocol_label": "scim",
        "include_scim_context": True,
    }
    generators = [
        SearchGenerator(intent=SearchIntent.ALL, **shared_kwargs),
        CreateGenerator(**shared_kwargs),
        UpdateGenerator(**shared_kwargs),
        DeleteGenerator(**shared_kwargs),
    ]

    assert all(generator.config.context_only_for_conndev for generator in generators)


@pytest.mark.asyncio
@pytest.mark.parametrize("include_unrelated_provider_document", [False, True])
async def test_scim_crud_runs_context_only_generation_when_selected_input_is_conndev(
    include_unrelated_provider_document: bool,
):
    raw_conndev_content = '{"rawConndevMarker":"must-not-reach-the-prompt"}'
    unrelated_provider_content = "Unrelated provider documentation must not reach the selected prompt."
    conndev_chunk_id = "conndev-only-chunk"
    generated_code = 'objectClass("User") { create { } }'
    generator = CreateGenerator(
        object_class="User",
        docs_text="SCIM create docs",
        system_prompt="System prompt",
        user_prompt="{chunk}",
        protocol_label="scim",
        include_scim_context=True,
    )
    chain = AsyncMock()
    chain.ainvoke.return_value = generated_code

    attributes: dict[str, Any] = {
        "attributes": {"userName": {"type": "string"}},
        "scimContext": {
            "schema": {
                "name": "User",
                "urn": "urn:ietf:params:scim:schemas:core:2.0:User",
                "attributes": [{"name": "userName", "type": "string"}],
            },
            "resource": {"name": "User", "endpoint": "/Users"},
            "extensions": [],
            "connectorObjectClass": {"name": "User", "attributes": []},
        },
    }
    endpoints = {
        "scimCapabilities": {"filter": {"supported": True}},
        "endpoints": [{"method": "POST", "path": "/Users", "description": "Create User"}],
    }
    documentation_items = [
        {
            "chunkId": conndev_chunk_id,
            "content": raw_conndev_content,
            "metadata": {"content_type": "application/com.evolveum.conndev+json"},
        }
    ]
    if include_unrelated_provider_document:
        documentation_items.append(
            {
                "chunkId": "unrelated-provider-chunk",
                "content": unrelated_provider_content,
                "metadata": {"content_type": "text/html"},
            }
        )

    with (
        patch.object(
            generator,
            "_load_documentation_items",
            new_callable=AsyncMock,
            return_value=documentation_items,
        ),
        patch.object(generator, "_build_llm_chain", return_value=chain),
        patch.object(
            generator,
            "_cleanup_generated_code",
            new_callable=AsyncMock,
            return_value=generated_code,
        ),
        patch("src.modules.codegen.core.base.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.codegen.core.base.increment_processed_documents", new_callable=AsyncMock),
        patch("src.modules.codegen.core.base.validate_groovy_code", return_value=None),
    ):
        result = await generator.generate(
            session_id=uuid4(),
            relevant_chunk_pairs=[{"chunk_id": conndev_chunk_id}],
            job_id=uuid4(),
            attributes=attributes,
            endpoints=endpoints,
        )

    assert result == generated_code
    chain.ainvoke.assert_awaited_once()
    prompt_vars = chain.ainvoke.await_args.args[0]
    assert prompt_vars["chunk"] == ""
    assert json.loads(prompt_vars["attributes_json"]) == [{"name": "userName", "type": "string"}]
    assert json.loads(prompt_vars["scim_protocol_schema_json"]) == attributes["scimContext"]["schema"]
    assert json.loads(prompt_vars["endpoints_json"]) == endpoints["endpoints"]
    assert raw_conndev_content not in json.dumps(prompt_vars)
    assert unrelated_provider_content not in json.dumps(prompt_vars)


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
        assert "<scim_service_provider_config>" in system_prompt
        assert "SCIM VS REST DSL BOUNDARY" in system_prompt
        assert "must never switch the output to REST DSL" in system_prompt
        assert "<extracted_endpoints>" not in system_prompt

    for user_prompt in user_prompts:
        assert "{scim_protocol_schema_json}" in user_prompt
        assert "{scim_resource_contract_json}" in user_prompt
        assert "{connid_object_class_json}" in user_prompt
        assert "{scim_service_provider_config_json}" in user_prompt
        assert "{endpoints_json}" not in user_prompt
        assert "{preferred_endpoints_json}" not in user_prompt
        assert "{base_api_url}" not in user_prompt


def test_scim_crud_prompts_enforce_operation_specific_native_dsl():
    create_prompt = " ".join(get_scim_create_system_prompt.split())
    update_prompt = " ".join(get_scim_update_system_prompt.split())
    delete_prompt = " ".join(get_scim_delete_system_prompt.split())

    assert "native `create {{ scim {{ ... }} }}`" in create_prompt
    assert "do not invent a POST endpoint" in create_prompt
    assert "Never generate `endpoint(...)` anywhere in SCIM output" in create_prompt

    assert "native `update {{ scim {{ put {{ ... }} patch {{ ... }} }} }}`" in update_prompt
    assert "Never represent PUT or PATCH as REST `endpoint(...)` blocks" in update_prompt
    assert "Never generate `endpoint(...)` anywhere in SCIM output" in update_prompt

    assert "minimal native `delete {{ }}` block" in delete_prompt
    assert "do not generate a REST endpoint or request block" in delete_prompt
    assert "Never generate `endpoint(...)` anywhere in SCIM output" in delete_prompt


def test_scim_search_prompts_enforce_native_scim_dsl_without_rest_endpoint_context():
    for system_prompt in [
        get_scim_search_all_system_prompt,
        get_scim_search_filter_system_prompt,
        get_scim_search_id_system_prompt,
    ]:
        assert "<search_docs> is the authoritative source for Groovy DSL structure" in system_prompt
        assert "The output is native SCIM DSL, never REST DSL" in system_prompt
        assert "inside `scim {{ limitations {{ ... }} }}`" in system_prompt
        assert 'objectClass("{object_class}") {{ search {{ scim {{ limitations {{ ... }} }} }} }}' in system_prompt
        assert "Never generate `endpoint(...)` anywhere in SCIM output" in system_prompt
        assert "<extracted_endpoints>" not in system_prompt

    assert "{endpoints_json}" not in get_scim_search_user_prompt
    assert "{preferred_endpoints_json}" not in get_scim_search_user_prompt
    assert "{base_api_url}" not in get_scim_search_user_prompt


def test_cleanup_prompt_distinguishes_native_scim_from_rest_dsl():
    prompt = " ".join(get_groovy_cleanup_system_prompt.split())

    assert "Never convert SCIM to REST" in prompt
    assert "apply exactly one matching section" in prompt
    assert "Rules from one section must never be applied to another section" in prompt
    assert "generic SCIM DSL is authoritative" in prompt
    assert "Native SCIM never uses `endpoint(...)`" in prompt
    assert "unwrap it and keep `search`, `create`, `update`, or `delete` directly below `objectClass`" in prompt
    assert "REST-only rules" in prompt


def test_scim_filter_prompt_distinguishes_missing_and_explicitly_disabled_capability():
    prompt = " ".join(get_scim_search_filter_system_prompt.split())

    assert "explicit `filter.supported: false` forbids" in prompt
    assert "whole contract is empty" in prompt
    assert "treat filtering support as unknown" in prompt
    assert "provider documentation in <chunk>" in prompt
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
        assert "endpoints_json" not in prompt.input_variables
        assert "preferred_endpoints_json" not in prompt.input_variables
        assert "base_api_url" not in prompt.input_variables
