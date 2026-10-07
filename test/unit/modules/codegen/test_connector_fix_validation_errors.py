# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Local validation errors of the connector-fix input scripts.

The caller usually sends exactly the code midPoint rejected, so an input script may fail
local validation. The fix does not reject it: the errors go to the model as faults to fix.
"""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.config import config
from src.modules.codegen.connector_fix import fix_connector_code
from src.modules.codegen.core.fix_connector import run_connector_fix_pass
from src.modules.codegen.errors import ConnectorFixContextTooLargeError
from src.modules.codegen.schema import ConnectorFixLLMResponse
from src.shared.enums import ApiType

CREATE_CODE = 'objectClass("user") {\n    create {\n    }\n}'
FIXED_CREATE = 'objectClass("user") {\n    create {\n        endpoint("/users")\n    }\n}'
BROKEN_NATIVE_SCHEMA = "objectClassesrt:\n  user:\n    connId:\n      UID: id"
FIXED_NATIVE_SCHEMA = "objectClasses:\n  user:\n    connId:\n      UID: id"
VALIDATION_ERROR = "objectClassesrt: Extra inputs are not permitted"

SCRIPTS = [
    {"operationKey": "userCreate", "kind": "create", "objectClass": "user", "code": CREATE_CODE},
    {"operationKey": "userNativeSchema", "kind": "nativeSchema", "objectClass": "user", "code": BROKEN_NATIVE_SCHEMA},
]


def _llm(**kwargs) -> ConnectorFixLLMResponse:
    return ConnectorFixLLMResponse.model_validate(kwargs)


def _reported_messages(errors: AsyncMock) -> list[str]:
    """Render every reported job error the way the job record stores it."""
    messages = []
    for call in errors.await_args_list:
        message, *args = call.args[2:]
        messages.append(message % tuple(args) if args else message)
    return messages


async def _run(pass_results, *, documentation="chunk text"):
    with (
        patch("src.modules.codegen.connector_fix.run_connector_fix_pass", new_callable=AsyncMock) as pass_mock,
        patch("src.modules.codegen.connector_fix.store_fixed_connector_scripts", new_callable=AsyncMock) as store,
        patch("src.modules.codegen.connector_fix.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.codegen.connector_fix.report_job_error", new_callable=AsyncMock) as errors,
        patch(
            "src.modules.codegen.connector_fix._load_escalation_documentation",
            new_callable=AsyncMock,
            return_value=documentation,
        ),
        patch(
            "src.modules.codegen.connector_fix.get_session_connection_target",
            new_callable=AsyncMock,
            return_value=("https://api.example.test", ""),
        ),
        patch("src.modules.codegen.connector_fix._load_dsl_documentation", return_value="docs"),
    ):
        pass_mock.side_effect = pass_results
        result = await fix_connector_code(
            scripts=SCRIPTS,
            midpoint_errors=["Unknown property objectClassesrt"],
            attributes={"attributes": {"Username": {"type": "string", "scimAttribute": "userName"}}},
            session_id=uuid4(),
            job_id=uuid4(),
            protocol=ApiType.REST,
        )
    return result, pass_mock, store, errors


@pytest.mark.asyncio
async def test_both_passes_receive_the_validation_errors_of_invalid_input_scripts_only():
    first = _llm(fixedScripts=[], needsDocumentation=True, documentationQuery="Which keys are allowed?")
    second = _llm(
        fixedScripts=[{"operationKey": "userNativeSchema", "code": FIXED_NATIVE_SCHEMA, "reason": "root key typo"}]
    )

    result, pass_mock, store, errors = await _run([first, second])

    assert pass_mock.await_count == 2
    for call in pass_mock.await_args_list:
        assert call.kwargs["validation_errors"] == {"userNativeSchema": (VALIDATION_ERROR,)}
    assert [change.operation_key for change in result.changed_operations] == ["userNativeSchema"]
    store.assert_awaited_once()
    errors.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_invalid_input_script_left_unfixed_is_reported():
    result, _, store, errors = await _run(
        [_llm(fixedScripts=[{"operationKey": "userCreate", "code": FIXED_CREATE, "reason": "missing endpoint"}])]
    )

    assert [change.operation_key for change in result.changed_operations] == ["userCreate"]
    # The session keeps its stored native schema; only the valid fix is written.
    assert list(store.await_args.args[1]) == ["userCreateOutput"]
    assert _reported_messages(errors) == [
        "[Codegen:Fix] userNativeSchema still fails local validation; no valid replacement was produced: "
        f"{VALIDATION_ERROR}"
    ]


async def _rendered_prompt(validation_errors) -> str:
    with (
        patch.object(config.codegen, "fix_max_input_tokens", 0),
        patch("src.modules.codegen.core.fix_connector.count_tokens", return_value=1) as count,
        patch("src.modules.codegen.core.fix_connector.build_structured_chain") as build_chain,
        pytest.raises(ConnectorFixContextTooLargeError),
    ):
        await run_connector_fix_pass(
            artifact_payloads=SCRIPTS,
            midpoint_errors=["Unknown property objectClassesrt"],
            validation_errors=validation_errors,
            protocol=ApiType.REST,
            connection_target="https://api.example.test",
            dsl_documentation="DSL reference",
            extracted_attributes="[]",
            extracted_endpoints="",
            sql_context=None,
            job_id=uuid4(),
        )
    build_chain.assert_not_called()
    return count.call_args.args[0]


@pytest.mark.asyncio
async def test_the_prompt_lists_every_validation_error_by_operation():
    rendered = await _rendered_prompt({"userNativeSchema": (VALIDATION_ERROR, "objectClasses: Field required")})

    assert "<validation_errors>" in rendered
    assert f"- userNativeSchema: {VALIDATION_ERROR}" in rendered
    assert "- userNativeSchema: objectClasses: Field required" in rendered


@pytest.mark.asyncio
async def test_the_prompt_has_no_validation_section_when_every_script_is_valid():
    rendered = await _rendered_prompt({})

    assert "<validation_errors>" not in rendered
