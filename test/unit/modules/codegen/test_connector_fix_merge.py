# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for validating and merging object-class fix output."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.modules.codegen.connector_fix import fix_connector_code
from src.modules.codegen.errors import ConnectorFixPassFailedError, ConnectorFixProducedNoValidScriptError
from src.modules.codegen.schema import ConnectorFixLLMResponse
from src.shared.enums import ApiType

CREATE_CODE = 'objectClass("user") {\n    create {\n    }\n}'
UPDATE_CODE = 'objectClass("user") {\n    update {\n    }\n}'
FIXED_UPDATE = 'objectClass("user") {\n    update {\n        endpoint("/users")\n    }\n}'


def _scripts() -> list[dict]:
    return [
        {"operationKey": "userCreate", "kind": "create", "objectClass": "user", "code": CREATE_CODE},
        {"operationKey": "userUpdate", "kind": "update", "objectClass": "user", "code": UPDATE_CODE},
    ]


def _llm(**kwargs) -> ConnectorFixLLMResponse:
    return ConnectorFixLLMResponse.model_validate(kwargs)


async def _run(response, scripts=None):
    """Run the worker with the LLM pass and every DB touch stubbed out."""
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
        if isinstance(response, Exception):
            pass_mock.side_effect = response
        else:
            pass_mock.return_value = response
        result = await fix_connector_code(
            scripts=scripts if scripts is not None else _scripts(),
            midpoint_errors=["No such method: request.pathParameter()"],
            attributes={"attributes": {"Username": {"type": "string", "scimAttribute": "userName"}}},
            session_id=uuid4(),
            job_id=uuid4(),
            protocol=ApiType.REST,
        )
    return result, store, errors


@pytest.mark.asyncio
async def test_untouched_scripts_are_returned_byte_identical_and_not_persisted():
    result, store, _ = await _run(
        _llm(fixedScripts=[{"operationKey": "userUpdate", "code": FIXED_UPDATE, "reason": "wrong method"}])
    )

    by_key = {script.operation_key: script.code for script in result.scripts}
    assert by_key["userCreate"] == CREATE_CODE
    assert by_key["userUpdate"] == FIXED_UPDATE
    assert [change.operation_key for change in result.changed_operations] == ["userUpdate"]

    store.assert_awaited_once()
    assert set(store.await_args.args[1]) == {"userUpdateOutput"}


@pytest.mark.asyncio
async def test_invalid_groovy_is_dropped_and_the_stored_script_kept():
    result, store, errors = await _run(
        _llm(
            fixedScripts=[
                {"operationKey": "userUpdate", "code": FIXED_UPDATE, "reason": "ok"},
                {"operationKey": "userCreate", "code": "objectClass( { { {", "reason": "broken"},
            ]
        )
    )

    assert {script.operation_key: script.code for script in result.scripts}["userCreate"] == CREATE_CODE
    assert [r.operation_key for r in result.rejected_scripts] == ["userCreate"]
    assert set(store.await_args.args[1]) == {"userUpdateOutput"}
    errors.assert_awaited()


@pytest.mark.asyncio
async def test_unknown_operation_key_never_becomes_a_session_key():
    result, store, _ = await _run(
        _llm(
            fixedScripts=[
                {"operationKey": "userUpdate", "code": FIXED_UPDATE, "reason": "ok"},
                {"operationKey": "somethingElse", "code": CREATE_CODE, "reason": "invented"},
            ]
        )
    )

    assert any(r.operation_key == "somethingElse" for r in result.rejected_scripts)
    assert set(store.await_args.args[1]) == {"userUpdateOutput"}


@pytest.mark.asyncio
async def test_a_script_identical_to_the_stored_one_is_not_a_change():
    result, store, _ = await _run(
        _llm(fixedScripts=[{"operationKey": "userUpdate", "code": UPDATE_CODE, "reason": "no change"}])
    )

    assert result.changed_operations == []
    assert [r.reason for r in result.rejected_scripts] == ["Proposed script is identical to the stored one."]
    store.assert_not_awaited()


@pytest.mark.asyncio
async def test_every_proposed_script_rejected_fails_the_job():
    with pytest.raises(ConnectorFixProducedNoValidScriptError) as exc_info:
        await _run(_llm(fixedScripts=[{"operationKey": "userUpdate", "code": "class {{{", "reason": "broken"}]))

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_nothing_to_fix_is_a_success_that_writes_nothing():
    result, store, _ = await _run(_llm(fixedScripts=[], analysis="The errors come from the midPoint config."))

    assert result.changed_operations == []
    assert result.rejected_scripts == []
    assert result.analysis == "The errors come from the midPoint config."
    assert len(result.scripts) == 2
    store.assert_not_awaited()


@pytest.mark.asyncio
async def test_operation_key_is_matched_case_insensitively():
    result, store, _ = await _run(
        _llm(fixedScripts=[{"operationKey": "UserUpdate", "code": FIXED_UPDATE, "reason": "ok"}])
    )

    assert [change.operation_key for change in result.changed_operations] == ["userUpdate"]
    assert set(store.await_args.args[1]) == {"userUpdateOutput"}


@pytest.mark.asyncio
async def test_a_failed_llm_pass_fails_instead_of_returning_unchanged_scripts():
    with pytest.raises(ConnectorFixPassFailedError) as exc_info:
        await _run(ConnectorFixPassFailedError())

    assert exc_info.value.status_code == 502
