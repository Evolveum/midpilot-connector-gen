# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Failure semantics for one structured connector-fix LLM pass."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.modules.codegen.core.fix_connector import run_connector_fix_pass
from src.modules.codegen.errors import ConnectorFixPassFailedError
from src.shared.enums import ApiType


async def _run_with_chain_response(response: object, errors: AsyncMock) -> None:
    chain = MagicMock()
    chain.ainvoke = AsyncMock(side_effect=response if isinstance(response, Exception) else None)
    if not isinstance(response, Exception):
        chain.ainvoke.return_value = response

    with (
        patch("src.modules.codegen.core.fix_connector.count_tokens", return_value=1),
        patch("src.modules.codegen.core.fix_connector.build_structured_chain", return_value=chain),
        patch("src.modules.codegen.core.fix_connector.append_job_error", new=errors),
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
            job_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_non_transient_chain_failure_is_recorded_and_raised():
    errors = AsyncMock()

    with pytest.raises(ConnectorFixPassFailedError):
        await _run_with_chain_response(ValueError("malformed structured output"), errors)

    errors.assert_awaited_once()
    assert "malformed structured output" in errors.await_args.args[1]


@pytest.mark.asyncio
async def test_unexpected_chain_response_type_is_recorded_and_raised():
    errors = AsyncMock()

    with pytest.raises(ConnectorFixPassFailedError):
        await _run_with_chain_response({"fixedScripts": []}, errors)

    errors.assert_awaited_once()
    assert "Unexpected fix response type: dict" in errors.await_args.args[1]
