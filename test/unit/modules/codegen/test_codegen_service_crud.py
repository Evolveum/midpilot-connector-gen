# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for codegen service CRUD generators."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.modules.codegen import service


@pytest.mark.asyncio
async def test_generate_create():
    """Test generating create code from attributes and endpoints."""
    test_attributes = {
        "username": {"type": "string", "description": "User's login name"},
        "email": {"type": "string", "format": "email", "description": "Email address"},
    }

    test_endpoints = {"endpoints": [{"method": "POST", "path": "/users", "description": "Create user"}]}
    test_preferred_endpoint = {"method": "POST", "path": "/users"}

    with (
        patch("src.modules.codegen.service.async_session_maker") as mock_session_maker,
        patch("src.modules.codegen.service.SessionRepository") as mock_session_repository,
        patch("src.modules.codegen.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
        patch("src.modules.codegen.service.get_session_base_api_url", new_callable=AsyncMock, return_value=""),
        patch("src.modules.codegen.service.CreateGenerator") as mock_create_generator_class,
    ):
        mock_db_cm = mock_session_maker.return_value
        mock_db = AsyncMock()
        mock_db_cm.__aenter__.return_value = mock_db

        mock_repo_instance = mock_session_repository.return_value
        mock_repo_instance.get_session_data = AsyncMock(return_value=None)

        # Mock the generator instance and its generate method (must be async)
        mock_generator_instance = mock_create_generator_class.return_value
        mock_generator_instance.generate = AsyncMock(return_value="mocked create code")

        result = await service.create_create(
            attributes=test_attributes,
            endpoints=test_endpoints,
            preferred_endpoint=test_preferred_endpoint,
            session_id=uuid4(),
            object_class="User",
            job_id=uuid4(),
        )

        assert isinstance(result, dict)
        assert "code" in result
        assert result["code"] == "mocked create code"

        # Verify generator was instantiated and generate method was called
        mock_create_generator_class.assert_called_once()
        _, kwargs = mock_create_generator_class.call_args
        assert kwargs["preferred_endpoint"] == test_preferred_endpoint
        mock_generator_instance.generate.assert_called_once()


@pytest.mark.asyncio
async def test_generate_update():
    """Test generating update code from attributes and endpoints."""
    test_attributes = {
        "username": {"type": "string", "description": "User's login name"},
        "email": {"type": "string", "format": "email", "description": "Email address"},
    }

    test_endpoints = {"endpoints": [{"method": "PUT", "path": "/users/{id}", "description": "Update user"}]}
    test_preferred_endpoint = {"method": "PATCH", "path": "/users/{id}"}

    with (
        patch("src.modules.codegen.service.async_session_maker") as mock_session_maker,
        patch("src.modules.codegen.service.SessionRepository") as mock_session_repository,
        patch("src.modules.codegen.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
        patch("src.modules.codegen.service.get_session_base_api_url", new_callable=AsyncMock, return_value=""),
        patch("src.modules.codegen.service.UpdateGenerator") as mock_update_generator_class,
    ):
        mock_db_cm = mock_session_maker.return_value
        mock_db = AsyncMock()
        mock_db_cm.__aenter__.return_value = mock_db

        mock_repo_instance = mock_session_repository.return_value
        mock_repo_instance.get_session_data = AsyncMock(return_value=None)

        # Mock the generator instance and its generate method (must be async)
        mock_generator_instance = mock_update_generator_class.return_value
        mock_generator_instance.generate = AsyncMock(return_value="mocked update code")

        result = await service.create_update(
            attributes=test_attributes,
            endpoints=test_endpoints,
            preferred_endpoint=test_preferred_endpoint,
            session_id=uuid4(),
            object_class="User",
            job_id=uuid4(),
        )

        assert isinstance(result, dict)
        assert "code" in result
        assert result["code"] == "mocked update code"

        # Verify generator was instantiated and generate method was called
        mock_update_generator_class.assert_called_once()
        _, kwargs = mock_update_generator_class.call_args
        assert kwargs["preferred_endpoint"] == test_preferred_endpoint
        mock_generator_instance.generate.assert_called_once()


@pytest.mark.asyncio
async def test_generate_delete():
    """Test generating delete code from attributes and endpoints."""
    test_attributes = {
        "id": {"type": "string", "format": "uuid", "description": "Unique ID"},
    }

    test_endpoints = {"endpoints": [{"method": "DELETE", "path": "/users/{id}", "description": "Delete user"}]}
    test_preferred_endpoint = {"method": "DELETE", "path": "/users/{id}"}

    with (
        patch("src.modules.codegen.service.async_session_maker") as mock_session_maker,
        patch("src.modules.codegen.service.SessionRepository") as mock_session_repository,
        patch("src.modules.codegen.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
        patch("src.modules.codegen.service.get_session_base_api_url", new_callable=AsyncMock, return_value=""),
        patch("src.modules.codegen.service.DeleteGenerator") as mock_delete_generator_class,
    ):
        mock_db_cm = mock_session_maker.return_value
        mock_db = AsyncMock()
        mock_db_cm.__aenter__.return_value = mock_db

        mock_repo_instance = mock_session_repository.return_value
        mock_repo_instance.get_session_data = AsyncMock(return_value=None)

        # Mock the generator instance and its generate method (must be async)
        mock_generator_instance = mock_delete_generator_class.return_value
        mock_generator_instance.generate = AsyncMock(return_value="mocked delete code")

        result = await service.create_delete(
            attributes=test_attributes,
            endpoints=test_endpoints,
            preferred_endpoint=test_preferred_endpoint,
            session_id=uuid4(),
            object_class="User",
            job_id=uuid4(),
        )

        assert isinstance(result, dict)
        assert "code" in result
        assert result["code"] == "mocked delete code"

        # Verify generator was instantiated and generate method was called
        mock_delete_generator_class.assert_called_once()
        _, kwargs = mock_delete_generator_class.call_args
        assert kwargs["preferred_endpoint"] == test_preferred_endpoint
        mock_generator_instance.generate.assert_called_once()
