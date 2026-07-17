# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for codegen service schema generators."""

import json
from unittest.mock import patch
from uuid import uuid4

import pytest

from src.common.enums import ApiType
from src.modules.codegen import generation
from src.modules.codegen.prompts.native_schema_prompts import (
    get_native_schema_system_prompt,
    get_native_schema_user_prompt,
)
from src.modules.codegen.prompts.scim.native_schema_prompts import (
    get_scim_native_schema_system_prompt,
    get_scim_native_schema_user_prompt,
)


def test_protocol_neutral_native_schema_prompts_contain_no_scim_context():
    prompt_text = get_native_schema_system_prompt + get_native_schema_user_prompt

    assert "SCIM" not in prompt_text
    assert "scim_protocol_schema" not in prompt_text
    assert "scim_resource_contract" not in prompt_text
    assert "connid_object_class" not in prompt_text


def test_scim_native_schema_prompts_add_scim_context_explicitly():
    prompt_text = get_scim_native_schema_system_prompt + get_scim_native_schema_user_prompt

    assert "SCIM CONTRACT CONTEXT RULES" in prompt_text
    assert "{scim_protocol_schema_json}" in prompt_text
    assert "{scim_resource_contract_json}" in prompt_text
    assert "{connid_object_class_json}" in prompt_text


@pytest.mark.asyncio
async def test_generate_native_schema():
    """Test generating native schema from attributes."""
    test_attributes = {
        "username": {"type": "string", "description": "User's login name", "mandatory": True},
        "id": {"type": "string", "format": "uuid", "description": "Unique identifier"},
    }

    with (
        patch("src.modules.codegen.generation.generate_groovy") as mock_generate_groovy,
    ):
        mock_generate_groovy.return_value = "mocked groovy code"

        result = await generation.generate_native_schema_code(
            test_attributes,
            "User",
            session_id=uuid4(),
            job_id=uuid4(),
            protocol=ApiType.REST,
        )

        assert isinstance(result, dict)
        assert "code" in result
        assert result["code"] == "mocked groovy code"

        mock_generate_groovy.assert_called_once()
        _, kwargs = mock_generate_groovy.call_args
        assert kwargs["system_prompt"] == get_native_schema_system_prompt
        assert kwargs["user_prompt"] == get_native_schema_user_prompt
        assert set(kwargs["extra_prompt_vars"]) == {"user_schema_docs"}


@pytest.mark.asyncio
async def test_generate_native_schema_uses_sql_docs_for_sql_api_type():
    test_attributes = {
        "username": {"type": "varchar", "description": "User login", "mandatory": True},
    }

    with (
        patch("src.modules.codegen.generation.generate_groovy") as mock_generate_groovy,
    ):
        mock_generate_groovy.return_value = "mocked sql schema code"

        result = await generation.generate_native_schema_code(
            test_attributes,
            "User",
            session_id=uuid4(),
            job_id=uuid4(),
            protocol=ApiType.SQL,
        )

    assert result == {"code": "mocked sql schema code"}
    _, kwargs = mock_generate_groovy.call_args
    assert "SQL native schema mapping" in kwargs["extra_prompt_vars"]["user_schema_docs"]
    assert kwargs["system_prompt"] == get_native_schema_system_prompt
    assert kwargs["user_prompt"] == get_native_schema_user_prompt
    assert set(kwargs["extra_prompt_vars"]) == {"user_schema_docs"}


@pytest.mark.asyncio
async def test_generate_native_schema_passes_bounded_scim_context_to_prompt():
    scim_context = {
        "className": "Device",
        "resource": {"endpoint": "/inventory/devices"},
        "schema": {"urn": "urn:example:Device", "attributes": [{"name": "serialNumber"}]},
        "extensions": [{"name": "DeviceExtension", "urn": "urn:example:DeviceExtension"}],
        "connectorObjectClass": {
            "name": "Device",
            "locator": "/inventory/devices",
            "attributes": [{"name": "serialNumber", "type": "string"}],
        },
    }
    payload = {
        "attributes": {"serialNumber": {"type": "string", "scimAttribute": "serialNumber"}},
        "scimContext": scim_context,
    }

    with patch("src.modules.codegen.generation.generate_groovy") as mock_generate_groovy:
        mock_generate_groovy.return_value = "mocked scim schema code"

        await generation.generate_native_schema_code(
            payload,
            "Device",
            session_id=uuid4(),
            job_id=uuid4(),
            protocol=ApiType.SCIM,
        )

    _, kwargs = mock_generate_groovy.call_args
    prompt_vars = kwargs["extra_prompt_vars"]
    assert kwargs["system_prompt"] == get_scim_native_schema_system_prompt
    assert kwargs["user_prompt"] == get_scim_native_schema_user_prompt
    assert json.loads(prompt_vars["scim_protocol_schema_json"]) == scim_context["schema"]
    assert json.loads(prompt_vars["scim_resource_contract_json"]) == {
        "resource": scim_context["resource"],
        "extensions": scim_context["extensions"],
    }
    assert json.loads(prompt_vars["connid_object_class_json"]) == scim_context["connectorObjectClass"]
    assert kwargs["records"][0]["scimAttribute"] == "serialNumber"


@pytest.mark.asyncio
async def test_generate_conn_id():
    """Test generating ConnID code from attributes."""
    test_attributes = {
        "username": {"type": "string", "description": "User's login name", "mandatory": True},
        "id": {"type": "string", "format": "uuid", "description": "Unique identifier"},
    }

    with patch("src.modules.codegen.generation.generate_groovy") as mock_generate_groovy:
        mock_generate_groovy.return_value = "mocked connid code"

        result = await generation.generate_conn_id_code(
            test_attributes,
            "User",
            job_id=uuid4(),
        )

        assert isinstance(result, dict)
        assert "code" in result
        assert result["code"] == "mocked connid code"

        mock_generate_groovy.assert_called_once()


@pytest.mark.asyncio
async def test_generate_conn_id_uses_scim_connector_projection():
    payload = {
        "attributes": {
            "userName": {"type": "string"},
            "emails": {"type": "UserEmails", "multivalue": True},
        },
        "scimContext": {
            "connectorObjectClass": {
                "attributes": [
                    {"name": "userName", "type": "string", "connectorExposed": True},
                    {"name": "id", "type": "string", "connectorExposed": True},
                ]
            }
        },
    }

    with patch("src.modules.codegen.generation.generate_groovy") as mock_generate_groovy:
        mock_generate_groovy.return_value = "mocked connid code"

        await generation.generate_conn_id_code(payload, "User", job_id=uuid4())

    _, kwargs = mock_generate_groovy.call_args
    assert [record["name"] for record in kwargs["records"]] == ["id", "userName"]
