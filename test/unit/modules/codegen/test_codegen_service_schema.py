# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for codegen service schema generators."""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from langchain_core.prompts import ChatPromptTemplate

from src.modules.codegen import generation
from src.modules.codegen.orchestration import schedule_native_schema_job
from src.modules.codegen.prompts.native_schema_prompts import (
    get_native_schema_system_prompt,
    get_native_schema_user_prompt,
)
from src.modules.codegen.prompts.scim.native_schema_prompts import (
    get_scim_native_schema_system_prompt,
    get_scim_native_schema_user_prompt,
)
from src.modules.codegen.prompts.sql.native_schema_prompts import (
    get_sql_native_schema_system_prompt,
    get_sql_native_schema_user_prompt,
)
from src.modules.codegen.selection.docs_loader import load_required_adoc_text
from src.modules.codegen.selection.protocol_selectors import get_operation_assets
from src.modules.codegen.utils.prompt_records import (
    build_attribute_context_records,
    build_complete_attribute_mapping_records,
    build_sql_context_prompt_vars,
    extract_sql_context,
)
from src.modules.digester.errors import SqlPhysicalSchemaNotFoundError
from src.modules.digester.schemas import AttributeResponse
from src.shared.enums import ApiType

_DOCUMENTATIONS_PACKAGE = "src.modules.codegen.documentations"

SQL_CONTEXT = {
    "physicalTable": {
        "databaseCatalog": "connector_project_db",
        "databaseSchema": "public",
        "table": "app_user",
        "tableType": "TABLE",
    },
    "connectorObjectClass": {
        "name": "app_user",
        "attributes": [
            {"name": "__NAME__", "connIdType": "string", "column": None},
            {"name": "login", "connIdType": "string", "column": "username"},
            {"name": "username", "connIdType": "string", "column": None},
        ],
    },
}


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
        assert set(kwargs["extra_prompt_vars"]) == {"protocol_schema_docs", "declarative_docs", "connid_attribute_docs"}


@pytest.mark.asyncio
async def test_generate_native_schema_uses_sql_docs_for_sql_api_type():
    test_attributes = {
        "attributes": {
            "nameorig": {
                "type": "string",
                "databaseType": "VARCHAR",
                "databaseCatalog": "database1",
                "databaseSchema": "identity",
                "table": "m_user",
                "column": "nameorig",
                "primaryKey": True,
                "foreignKey": {
                    "constraintName": "m_user_nameorig_fkey",
                    "referencedTable": "m_name",
                    "referencedColumn": "nameorig",
                },
            }
        },
        "sqlContext": {
            "physicalTable": {
                "databaseCatalog": "database1",
                "databaseSchema": "identity",
                "table": "m_user",
                "tableType": "TABLE",
            },
            "connectorObjectClass": {
                "name": "User",
                "attributes": [{"name": "Username", "connIdType": "string", "column": "nameorig"}],
            },
        },
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
    assert kwargs["system_prompt"] == get_sql_native_schema_system_prompt
    assert kwargs["user_prompt"] == get_sql_native_schema_user_prompt
    assert set(kwargs["extra_prompt_vars"]) == {
        "protocol_schema_docs",
        "declarative_docs",
        "connid_attribute_docs",
        "sql_physical_table_json",
        "sql_connector_object_class_json",
    }
    assert kwargs["records"][0]["databaseCatalog"] == "database1"
    assert kwargs["records"][0]["databaseSchema"] == "identity"
    assert kwargs["records"][0]["table"] == "m_user"
    assert kwargs["records"][0]["column"] == "nameorig"
    assert kwargs["records"][0]["primaryKey"] is True
    assert kwargs["records"][0]["foreignKey"] == {
        "constraintName": "m_user_nameorig_fkey",
        "referencedTable": "m_name",
        "referencedColumn": "nameorig",
    }
    assert kwargs["records"][0]["databaseType"] == "VARCHAR"
    assert json.loads(kwargs["extra_prompt_vars"]["sql_physical_table_json"])["table"] == "m_user"
    assert json.loads(kwargs["extra_prompt_vars"]["sql_connector_object_class_json"])["attributes"] == [
        {"name": "Username", "connIdType": "string", "column": "nameorig"}
    ]


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


@pytest.mark.parametrize(
    ("protocol", "expected_extra_variables"),
    [
        (ApiType.REST, set()),
        (ApiType.SQL, {"sql_physical_table_json", "sql_connector_object_class_json"}),
        (
            ApiType.SCIM,
            {
                "scim_protocol_schema_json",
                "scim_resource_contract_json",
                "connid_object_class_json",
                "scim_service_provider_config_json",
            },
        ),
    ],
)
def test_native_schema_prompt_renders_with_all_expected_variables(protocol, expected_extra_variables):
    """
    Guards the merged prompt against an unescaped brace.

    The Groovy examples in these rules contain literal braces, which a LangChain
    f-string template reads as placeholders unless they are doubled. Getting that
    wrong fails inside the background job, not at import, so it is asserted here.
    """
    assets = get_operation_assets("native_schema", protocol)

    template = ChatPromptTemplate.from_messages([("system", assets.system_prompt), ("human", assets.user_prompt)])

    assert (
        set(template.input_variables)
        == {
            "object_class",
            "records_json",
            "protocol_schema_docs",
            "declarative_docs",
            "connid_attribute_docs",
            "repair_system_suffix",
            "repair_user_suffix",
        }
        | expected_extra_variables
    )


@pytest.mark.parametrize("protocol", [ApiType.REST, ApiType.SCIM, ApiType.SQL])
def test_native_schema_prompt_states_documentation_precedence(protocol):
    """Both reference slots exist and the protocol's own DSL is named as the syntax authority."""
    system_prompt = get_operation_assets("native_schema", protocol).system_prompt

    assert "<protocol_schema_docs>" in system_prompt
    assert "<connid_attribute_docs>" in system_prompt
    assert "DOCUMENTATION PRECEDENCE:" in system_prompt
    assert "CONNID MAPPING:" in system_prompt
    assert "Take the meaning" in system_prompt


@pytest.mark.parametrize("protocol", [ApiType.REST, ApiType.SCIM, ApiType.SQL])
def test_connid_reference_is_resolved_for_every_native_schema_protocol(protocol):
    assets = get_operation_assets("native_schema", protocol)

    assert assets.connid_docs_path == "connid-attributes.adoc"
    assert load_required_adoc_text(_DOCUMENTATIONS_PACKAGE, assets.connid_docs_path)


def test_sql_native_schema_documentation_never_shows_connid_attribute_calls():
    """
    Canary for the single-document decision.

    SQL declares ConnID names as a nested ``connId { name "__UID__" }`` block. The
    shared ConnID reference shows the ``connIdAttribute(...)`` call instead, and the
    prompt resolves that conflict by making this document the syntax authority. If
    ``connIdAttribute`` ever appears here, that rule starts selecting the wrong form.
    """
    sql_schema_docs = load_required_adoc_text(
        _DOCUMENTATIONS_PACKAGE, get_operation_assets("native_schema", ApiType.SQL).docs_path
    )

    assert "connIdAttribute" not in sql_schema_docs
    assert 'connId { name "__UID__" }' in sql_schema_docs


def test_sql_context_is_typed_and_excluded_from_crud_attribute_records():
    payload = AttributeResponse.model_validate(
        {
            "attributes": {
                "id": {
                    "type": "integer",
                    "databaseType": "SERIAL",
                    "nullable": False,
                    "unique": True,
                    "generated": True,
                    "table": "app_user",
                    "column": "id",
                    "primaryKey": True,
                }
            },
            "sqlContext": SQL_CONTEXT,
        }
    )

    context = extract_sql_context(payload)
    assert context["physicalTable"]["table"] == "app_user"
    assert context["connectorObjectClass"]["attributes"][0]["name"] == "__NAME__"
    assert [record["name"] for record in build_attribute_context_records(payload)] == ["id"]
    assert [record["name"] for record in build_complete_attribute_mapping_records(payload)] == ["id"]
    assert "__NAME__" not in json.dumps(build_attribute_context_records(payload))
    prompt_vars = build_sql_context_prompt_vars(payload)
    assert json.loads(prompt_vars["sql_physical_table_json"])["databaseSchema"] == "public"
    assert json.loads(prompt_vars["sql_connector_object_class_json"])["attributes"][0]["column"] is None


@pytest.mark.asyncio
async def test_native_schema_scheduling_rejects_stale_sql_attributes_without_context():
    repo = MagicMock()
    repo.get_session_data = AsyncMock(
        return_value={"attributes": {"__NAME__": {"table": "app_user", "column": "__NAME__"}}}
    )
    with (
        patch(
            "src.modules.codegen.orchestration.resolve_effective_api_type",
            new_callable=AsyncMock,
            return_value=ApiType.SQL,
        ),
        patch("src.modules.codegen.orchestration.schedule_coroutine_job", new_callable=AsyncMock) as schedule,
        pytest.raises(SqlPhysicalSchemaNotFoundError) as exc_info,
    ):
        await schedule_native_schema_job(
            repo=repo,
            session_id=uuid4(),
            object_class="app_user",
            api_type=None,
            skip_cache=False,
            codegen_input=None,
        )

    assert exc_info.value.status_code == 422
    assert "Rerun attribute extraction" in str(exc_info.value)
    schedule.assert_not_awaited()
