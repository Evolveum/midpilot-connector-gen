# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Unit tests for recovering ``<script>`` tags the model echoes back into fixed code.

``render_script_bundle`` wraps every input artifact in a ``<script operationKey="..."
kind="..." objectClass="...">`` tag so several scripts fit one prompt without JSON
escaping. The model is asked to return bare code, but it does not always comply, and
when it does not, it can echo more than one tag under a single ``fixedScripts`` entry.
"""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.modules.codegen.connector_fix import fix_connector_code
from src.modules.codegen.errors import ConnectorFixProducedNoValidScriptError
from src.modules.codegen.schema import ConnectorFixLLMResponse
from src.shared.enums import ApiType

CREATE_CODE = 'objectClass("user") {\n    create {\n    }\n}'
UPDATE_CODE = 'objectClass("user") {\n    update {\n    }\n}'
FIXED_CREATE = 'objectClass("user") {\n    create {\n        endpoint("/users")\n    }\n}'
FIXED_UPDATE = 'objectClass("user") {\n    update {\n        endpoint("/users")\n    }\n}'
FIXED_NATIVE_SCHEMA = "objectClasses:\n  user:\n    connId:\n      UID: id\n"


def _scripts() -> list[dict]:
    return [
        {"operationKey": "userCreate", "kind": "create", "objectClass": "user", "code": CREATE_CODE},
        {"operationKey": "userUpdate", "kind": "update", "objectClass": "user", "code": UPDATE_CODE},
        {
            "operationKey": "userNativeSchema",
            "kind": "nativeSchema",
            "objectClass": "user",
            "code": "objectClassess:\n  user:\n    connId:\n      UID: id\n",
        },
    ]


def _llm(**kwargs) -> ConnectorFixLLMResponse:
    return ConnectorFixLLMResponse.model_validate(kwargs)


def _reported(errors: AsyncMock) -> str:
    """Render the last reported job error the way the job record stores it."""
    message, *args = errors.await_args_list[-1].args[2:]
    return message % tuple(args) if args else message


async def _run(response):
    """Run the worker; on a hard failure, return the exception instead of a result."""
    with (
        patch("src.modules.codegen.connector_fix.run_connector_fix_pass", new_callable=AsyncMock) as pass_mock,
        patch("src.modules.codegen.connector_fix.store_fixed_connector_scripts", new_callable=AsyncMock) as store,
        patch("src.modules.codegen.connector_fix.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.codegen.connector_fix.report_job_error", new_callable=AsyncMock) as errors,
        patch(
            "src.modules.codegen.connector_fix.get_session_connection_target",
            new_callable=AsyncMock,
            return_value=("https://api.example.test", ""),
        ),
        patch("src.modules.codegen.connector_fix._load_dsl_documentation", return_value="docs"),
    ):
        pass_mock.return_value = response
        try:
            result = await fix_connector_code(
                scripts=_scripts(),
                midpoint_errors=["unknown key 'objectClassess'"],
                attributes={"attributes": {"Username": {"type": "string", "scimAttribute": "userName"}}},
                session_id=uuid4(),
                job_id=uuid4(),
                protocol=ApiType.REST,
            )
        except Exception as exc:
            result = exc
    return result, store, errors


@pytest.mark.asyncio
async def test_a_script_echoing_its_own_input_tag_is_unwrapped_and_accepted():
    """Regression test: the exact shape that failed validation before the tag was stripped."""
    wrapped = f'<script operationKey="userNativeSchema" kind="nativeSchema" objectClass="user">\n{FIXED_NATIVE_SCHEMA}\n</script>'
    result, store, errors = await _run(
        _llm(fixedScripts=[{"operationKey": "userNativeSchema", "code": wrapped, "reason": "fixed typo'd key"}])
    )

    by_key = {script.operation_key: script.code for script in result.scripts}
    assert by_key["userNativeSchema"] == FIXED_NATIVE_SCHEMA.strip()
    assert "<script" not in by_key["userNativeSchema"]
    assert [change.operation_key for change in result.changed_operations] == ["userNativeSchema"]
    assert result.rejected_scripts == []
    store.assert_awaited_once()
    errors.assert_not_awaited()


@pytest.mark.asyncio
async def test_one_entry_echoing_two_tags_is_split_into_two_accepted_operations():
    """The model may bundle more than one revised artifact under a single fixedScripts entry."""
    bundled = (
        f'<script operationKey="userCreate" kind="create" objectClass="user">\n{FIXED_CREATE}\n</script>\n\n'
        f'<script operationKey="userUpdate" kind="update" objectClass="user">\n{FIXED_UPDATE}\n</script>'
    )
    result, store, _ = await _run(
        _llm(fixedScripts=[{"operationKey": "userCreate", "code": bundled, "reason": "endpoint fix"}])
    )

    by_key = {script.operation_key: script.code for script in result.scripts}
    assert by_key["userCreate"] == FIXED_CREATE
    assert by_key["userUpdate"] == FIXED_UPDATE
    assert sorted(change.operation_key for change in result.changed_operations) == ["userCreate", "userUpdate"]
    assert set(store.await_args.args[1]) == {"userCreateOutput", "userUpdateOutput"}


@pytest.mark.asyncio
async def test_a_tag_kind_that_disagrees_with_the_resolved_operation_is_rejected():
    """A tag naming the wrong kind for its operationKey must not be trusted silently."""
    mismatched = f'<script operationKey="userCreate" kind="nativeSchema" objectClass="user">\n{FIXED_CREATE}\n</script>'
    result, store, errors = await _run(
        _llm(fixedScripts=[{"operationKey": "userCreate", "code": mismatched, "reason": "bogus tag"}])
    )

    assert isinstance(result, ConnectorFixProducedNoValidScriptError)
    assert "does not match" in _reported(errors)
    store.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_tag_object_class_that_disagrees_with_the_resolved_operation_is_rejected():
    mismatched = f'<script operationKey="userCreate" kind="create" objectClass="group">\n{FIXED_CREATE}\n</script>'
    result, store, errors = await _run(
        _llm(fixedScripts=[{"operationKey": "userCreate", "code": mismatched, "reason": "bogus tag"}])
    )

    assert isinstance(result, ConnectorFixProducedNoValidScriptError)
    assert "does not match" in _reported(errors)
    store.assert_not_awaited()
