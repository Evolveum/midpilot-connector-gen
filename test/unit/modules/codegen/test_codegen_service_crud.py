# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for codegen service operation generators."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.modules.codegen import generation
from src.modules.codegen.enums import SearchIntent
from src.modules.codegen.prompts.sql.create_prompts import get_sql_create_system_prompt
from src.shared.enums import ApiType

_ATTRIBUTES = {
    "username": {"type": "string", "description": "User's login name"},
    "email": {"type": "string", "format": "email", "description": "Email address"},
}
_ENDPOINTS = {"endpoints": [{"method": "GET", "path": "/users"}]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("generate_code", "generator_name", "preferred_endpoints", "extra_kwargs"),
    [
        (
            generation.generate_create_code,
            "CreateGenerator",
            [{"method": "POST", "path": "/users"}, {"method": "POST", "path": "/users/create"}],
            {},
        ),
        (
            generation.generate_update_code,
            "UpdateGenerator",
            [{"method": "PATCH", "path": "/users/{id}"}, {"method": "PUT", "path": "/users/{id}"}],
            {},
        ),
        (
            generation.generate_delete_code,
            "DeleteGenerator",
            [{"method": "DELETE", "path": "/users/{id}"}],
            {},
        ),
        (
            generation.generate_search_code,
            "SearchGenerator",
            [{"method": "GET", "path": "/users/search"}, {"method": "GET", "path": "/users/{id}"}],
            {"intent": SearchIntent.FILTER},
        ),
    ],
    ids=["create", "update", "delete", "search"],
)
async def test_operation_generation_delegates_to_its_generator(
    generate_code,
    generator_name: str,
    preferred_endpoints: list[dict],
    extra_kwargs: dict,
):
    """Every operation resolves its connection target and hands the request to its own generator."""
    session_id = uuid4()

    with (
        patch(
            "src.modules.codegen.generation.get_session_connection_target",
            new_callable=AsyncMock,
            return_value=("", ""),
        ) as mock_get_connection_target,
        patch("src.modules.codegen.generation._collect_relevant_chunks", new_callable=AsyncMock, return_value=None),
        patch(f"src.modules.codegen.generation.{generator_name}") as mock_generator_class,
    ):
        mock_generator_instance = mock_generator_class.return_value
        mock_generator_instance.generate = AsyncMock(return_value="mocked code")

        result = await generate_code(
            attributes=_ATTRIBUTES,
            endpoints=_ENDPOINTS,
            preferred_endpoints=preferred_endpoints,
            session_id=session_id,
            object_class="User",
            job_id=uuid4(),
            protocol=ApiType.REST,
            **extra_kwargs,
        )

    assert result == {"code": "mocked code"}
    mock_get_connection_target.assert_awaited_once_with(session_id, protocol=ApiType.REST)
    mock_generator_class.assert_called_once()
    generator_kwargs = mock_generator_class.call_args.kwargs
    assert generator_kwargs["preferred_endpoints"] == preferred_endpoints
    for name, value in extra_kwargs.items():
        assert generator_kwargs[name] == value
    mock_generator_instance.generate.assert_called_once()


@pytest.mark.asyncio
async def test_generate_create_uses_sql_assets_for_sql_api_type():
    test_attributes = {"username": {"type": "varchar", "description": "User login"}}
    test_tables = {"endpoints": [{"table": "users", "columns": [{"name": "username", "type": "varchar"}]}]}

    session_id = uuid4()
    job_id = uuid4()

    with (
        patch(
            "src.modules.codegen.generation.get_session_connection_target",
            new_callable=AsyncMock,
            return_value=("", ""),
        ) as mock_get_connection_target,
        patch("src.modules.codegen.generation._collect_relevant_chunks", new_callable=AsyncMock, return_value=None),
        patch("src.modules.codegen.generation.CreateGenerator") as mock_create_generator_class,
    ):
        mock_generator_instance = mock_create_generator_class.return_value
        mock_generator_instance.generate = AsyncMock(return_value="mocked sql create code")

        result = await generation.generate_create_code(
            attributes=test_attributes,
            endpoints=test_tables,
            session_id=session_id,
            object_class="User",
            job_id=job_id,
            protocol=ApiType.SQL,
        )

    assert result == {"code": "mocked sql create code"}
    mock_get_connection_target.assert_awaited_once_with(session_id, protocol=ApiType.SQL)
    _, kwargs = mock_create_generator_class.call_args
    assert kwargs["system_prompt"] == get_sql_create_system_prompt
    assert kwargs["protocol_label"] == "sql"
