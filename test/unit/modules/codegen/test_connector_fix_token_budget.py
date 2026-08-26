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
