# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for codegen service search generator."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.modules.codegen import service
from src.modules.codegen.enums import SearchIntent


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

    with (
        patch("src.modules.codegen.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
        patch("src.modules.codegen.service.get_session_base_api_url", new_callable=AsyncMock, return_value=""),
        patch(
            "src.modules.codegen.service._collect_relevant_chunks", new_callable=AsyncMock, return_value=(None, None)
        ),
        patch("src.modules.codegen.service.SearchGenerator") as mock_search_generator_class,
    ):
        # Mock the generator instance and its generate method (must be async)
        mock_generator_instance = mock_search_generator_class.return_value
        mock_generator_instance.generate = AsyncMock(return_value="mocked search code")

        result = await service.create_search(
            attributes=test_attributes,
            endpoints=test_endpoints,
            preferred_endpoints=test_preferred_endpoints,
            session_id=uuid4(),
            object_class="User",
            intent=SearchIntent.FILTER,
            job_id=uuid4(),
        )

        assert isinstance(result, dict)
        assert "code" in result
        assert result["code"] == "mocked search code"

        # Verify generator was instantiated and generate method was called
        mock_search_generator_class.assert_called_once()
        _, kwargs = mock_search_generator_class.call_args
        assert kwargs["intent"] == SearchIntent.FILTER
        assert kwargs["preferred_endpoints"] == test_preferred_endpoints
        mock_generator_instance.generate.assert_called_once()
