# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for codegen service search generator."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.modules.codegen import generation
from src.modules.codegen.core.base import BaseGroovyGenerator
from src.modules.codegen.enums import SearchIntent
from src.modules.codegen.schema import CodegenRepairContext
from src.shared.enums import ApiType


@pytest.mark.asyncio
async def test_generate_search():
    """Test generating search code from attributes and endpoints."""
    test_attributes = {
        "username": {"type": "string", "description": "User's login name"},
        "id": {"type": "string", "format": "uuid", "description": "Unique ID"},
    }

    test_endpoints = {"endpoints": [{"method": "GET", "path": "/users", "description": "List users"}]}
    test_preferred_endpoints = [
        {"method": "GET", "path": "/users/search"},
        {"method": "GET", "path": "/users/{id}"},
    ]

    session_id = uuid4()
    job_id = uuid4()

    with (
        patch(
            "src.modules.codegen.generation.get_session_connection_target",
            new_callable=AsyncMock,
            return_value=("", ""),
        ) as mock_get_connection_target,
        patch("src.modules.codegen.generation._collect_relevant_chunks", new_callable=AsyncMock, return_value=None),
        patch("src.modules.codegen.generation.SearchGenerator") as mock_search_generator_class,
    ):
        # Mock the generator instance and its generate method (must be async)
        mock_generator_instance = mock_search_generator_class.return_value
        mock_generator_instance.generate = AsyncMock(return_value="mocked search code")

        result = await generation.generate_search_code(
            attributes=test_attributes,
            endpoints=test_endpoints,
            preferred_endpoints=test_preferred_endpoints,
            session_id=session_id,
            object_class="User",
            intent=SearchIntent.FILTER,
            job_id=job_id,
            protocol=ApiType.REST,
        )

        assert isinstance(result, dict)
        assert "code" in result
        assert result["code"] == "mocked search code"

        # Verify generator was instantiated and generate method was called
        mock_search_generator_class.assert_called_once()
        _, kwargs = mock_search_generator_class.call_args
        assert kwargs["intent"] == SearchIntent.FILTER
        assert kwargs["preferred_endpoints"] == test_preferred_endpoints
        mock_get_connection_target.assert_awaited_once_with(session_id, protocol=ApiType.REST)
        mock_generator_instance.generate.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", [ApiType.REST, ApiType.SCIM, ApiType.SQL])
@pytest.mark.parametrize(
    ("intent", "label"), [(SearchIntent.ALL, "All"), (SearchIntent.FILTER, "Filter"), (SearchIntent.ID, "Id")]
)
@pytest.mark.parametrize("repair", [False, True])
async def test_search_invocations_preserve_protocol_intent_and_repair_names(protocol, intent, label, repair):
    chain = AsyncMock()
    chain.ainvoke.return_value = ""
    context = CodegenRepairContext(current_script="broken(", midpoint_errors=["syntax"]) if repair else None
    with (
        patch("src.modules.codegen.generation.get_session_connection_target", new=AsyncMock(return_value=("", ""))),
        patch("src.modules.codegen.generation._collect_relevant_chunks", new=AsyncMock(return_value=None)),
        patch.object(BaseGroovyGenerator, "_load_documentation_items", new=AsyncMock(return_value=[])),
        patch.object(BaseGroovyGenerator, "_build_chunks", return_value=(["doc"], [], {}, [])),
        patch.object(BaseGroovyGenerator, "_initialize_progress", new_callable=AsyncMock),
        patch.object(BaseGroovyGenerator, "_build_llm_chain", return_value=chain),
        patch("src.modules.codegen.core.base.append_job_error", new_callable=AsyncMock),
        patch("src.modules.codegen.core.base.increment_processed_documents", new_callable=AsyncMock),
    ):
        await generation.generate_search_code(
            attributes={"attributes": {}},
            object_class="User",
            intent=intent,
            protocol=protocol,
            session_id=uuid4(),
            job_id=uuid4(),
            repair_context=context,
        )

    name = f"Codegen:Search:{protocol.value.upper()}:{label}" + (":Repair" if repair else "")
    assert [call.kwargs["config"]["run_name"] for call in chain.ainvoke.await_args_list] == [name]
