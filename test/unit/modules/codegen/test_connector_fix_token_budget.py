# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Token-budget checks for the assembled connector-fix prompt."""

from unittest.mock import patch
from uuid import uuid4

import pytest

from src.config import config
from src.modules.codegen.core.fix_connector import run_connector_fix_pass
from src.modules.codegen.errors import ConnectorFixContextTooLargeError
from src.shared.enums import ApiType


@pytest.mark.asyncio
async def test_complete_prompt_is_rejected_before_the_llm_chain_is_built():
    with (
        patch.object(config.codegen, "fix_max_input_tokens", 120_000),
        patch("src.modules.codegen.core.fix_connector.count_tokens", return_value=120_001) as count,
        patch("src.modules.codegen.core.fix_connector.build_structured_chain") as build_chain,
        pytest.raises(ConnectorFixContextTooLargeError) as exc_info,
    ):
        await run_connector_fix_pass(
            artifact_payloads=[
                {
                    "operationKey": "userUpdate",
                    "kind": "update",
                    "objectClass": "user",
                    "code": 'objectClass("user") { update {} }',
                }
            ],
            midpoint_errors=["Unsupported update request"],
            protocol=ApiType.REST,
            connection_target="https://api.example.test",
            dsl_documentation="DSL reference",
            extracted_attributes='[{"name": "Username", "scimAttribute": "userName"}]',
            extracted_endpoints="",
            sql_context=None,
            documentation_query="How is update encoded?",
            documentation_chunks="relevant vendor documentation",
            job_id=uuid4(),
        )

    assert exc_info.value.status_code == 413
    build_chain.assert_not_called()
    rendered_input = count.call_args.args[0]
    assert "Unsupported update request" in rendered_input
    assert "DSL reference" in rendered_input
    assert "relevant vendor documentation" in rendered_input
    assert "<physical_sql_table>" not in rendered_input
    assert "<connid_object_class_projection>" not in rendered_input


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", [ApiType.REST, ApiType.SCIM])
async def test_non_sql_prompt_does_not_render_sql_context(protocol):
    with (
        patch.object(config.codegen, "fix_max_input_tokens", 0),
        patch("src.modules.codegen.core.fix_connector.count_tokens", return_value=1) as count,
        patch("src.modules.codegen.core.fix_connector.build_structured_chain") as build_chain,
        pytest.raises(ConnectorFixContextTooLargeError),
    ):
        await run_connector_fix_pass(
            artifact_payloads=[],
            midpoint_errors=[],
            protocol=protocol,
            connection_target="https://api.example.test",
            dsl_documentation="DSL reference",
            extracted_attributes="[]",
            extracted_endpoints="",
            sql_context=None,
            job_id=uuid4(),
        )

    build_chain.assert_not_called()
    rendered_input = count.call_args.args[0]
    assert "<physical_sql_table>" not in rendered_input
    assert "<connid_object_class_projection>" not in rendered_input


@pytest.mark.asyncio
async def test_sql_context_is_rendered_before_token_budget_enforcement():
    sql_context = {
        "sql_physical_table_json": '{"databaseSchema":"public","table":"app_user"}',
        "sql_connector_object_class_json": (
            '{"name":"app_user","attributes":[{"name":"__NAME__","column":null},{"name":"login","column":"username"}]}'
        ),
    }
    with (
        patch.object(config.codegen, "fix_max_input_tokens", 0),
        patch("src.modules.codegen.core.fix_connector.count_tokens", return_value=1) as count,
        patch("src.modules.codegen.core.fix_connector.build_structured_chain") as build_chain,
        pytest.raises(ConnectorFixContextTooLargeError),
    ):
        await run_connector_fix_pass(
            artifact_payloads=[],
            midpoint_errors=[],
            protocol=ApiType.SQL,
            connection_target="connector_project_db",
            dsl_documentation="SQL DSL reference",
            extracted_attributes='[{"name":"username","column":"username","databaseType":"VARCHAR"}]',
            extracted_endpoints="",
            sql_context=sql_context,
            job_id=uuid4(),
        )

    build_chain.assert_not_called()
    rendered_input = count.call_args.args[0]
    assert "<physical_sql_table>" in rendered_input
    assert sql_context["sql_physical_table_json"] in rendered_input
    assert "<connid_object_class_projection>" in rendered_input
    assert sql_context["sql_connector_object_class_json"] in rendered_input
    assert "column: null" in rendered_input
