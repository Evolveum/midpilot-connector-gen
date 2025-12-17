# Copyright (c) 2025 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for the codegen service module."""

from unittest.mock import AsyncMock, patch

import pytest

from src.modules.codegen import service


@pytest.mark.asyncio
async def test_generate_native_schema():
    """Test generating native schema from attributes."""
    test_attributes = {
        "username": {"type": "string", "description": "User's login name", "mandatory": True},
        "id": {"type": "string", "format": "uuid", "description": "Unique identifier"},
    }

    with patch("src.modules.codegen.service.generate_groovy") as mock_generate_groovy:
        mock_generate_groovy.return_value = "mocked groovy code"

        result = await service.create_native_schema(
            test_attributes,
            "User",
            job_id="test-job-id",
        )

        assert isinstance(result, dict)
        assert "code" in result
        assert result["code"] == "mocked groovy code"

        mock_generate_groovy.assert_called_once()


@pytest.mark.asyncio
async def test_generate_conn_id():
    """Test generating ConnID code from attributes."""
    test_attributes = {
        "username": {"type": "string", "description": "User's login name", "mandatory": True},
        "id": {"type": "string", "format": "uuid", "description": "Unique identifier"},
    }

    with patch("src.modules.codegen.service.generate_groovy") as mock_generate_groovy:
        mock_generate_groovy.return_value = "mocked connid code"

        result = await service.create_conn_id(
            test_attributes,
            "User",
            job_id="test-job-id",
        )

        assert isinstance(result, dict)
        assert "code" in result
        assert result["code"] == "mocked connid code"

        mock_generate_groovy.assert_called_once()


@pytest.mark.asyncio
async def test_generate_search():
    """Test generating search code from attributes and endpoints."""
    test_attributes = {
        "username": {"type": "string", "description": "User's login name"},
        "id": {"type": "string", "format": "uuid", "description": "Unique ID"},
    }

    test_endpoints = {"endpoints": [{"method": "GET", "path": "/users", "description": "List users"}]}
    test_docs = [{"uuid": "doc1", "content": "Test documentation"}]

    with (
        patch("src.modules.codegen.service.SessionManager") as mock_session_manager,
        patch("src.modules.codegen.service.SearchGenerator") as mock_search_generator_class,
    ):
        mock_session_manager.get_session_data.return_value = None

        # Mock the generator instance and its generate method (must be async)
        mock_generator_instance = mock_search_generator_class.return_value
        mock_generator_instance.generate = AsyncMock(return_value="mocked search code")

        result = await service.create_search(
            attributes=test_attributes,
            endpoints=test_endpoints,
            documentation_items=test_docs,
            documentation="joined docs",
            session_id="test-session",
            object_class="User",
            job_id="test-job-id",
        )

        assert isinstance(result, dict)
        assert "code" in result
        assert result["code"] == "mocked search code"

        # Verify generator was instantiated and generate method was called
        mock_search_generator_class.assert_called_once()
        mock_generator_instance.generate.assert_called_once()


@pytest.mark.asyncio
async def test_generate_relation():
    """Test generating relation code."""
    test_relations = {"relations": [{"from": "User", "to": "Group", "type": "membership"}]}
    test_docs = [{"uuid": "doc1", "content": "Test documentation"}]

    with (
        patch("src.modules.codegen.service.SessionManager") as mock_session_manager,
        patch("src.modules.codegen.service.RelationGenerator") as mock_relation_generator_class,
    ):
        mock_session_manager.get_session_data.return_value = None

        # Mock the generator instance and its generate method (must be async)
        mock_generator_instance = mock_relation_generator_class.return_value
        mock_generator_instance.generate = AsyncMock(return_value="mocked relation code")

        result = await service.create_relation(
            relations=test_relations,
            documentation_items=test_docs,
            documentation="joined docs",
            session_id="test-session",
            job_id="test-job-id",
        )

        assert isinstance(result, dict)
        assert "code" in result
        assert result["code"] == "mocked relation code"

        # Verify generator was instantiated and generate method was called
        mock_relation_generator_class.assert_called_once()
        mock_generator_instance.generate.assert_called_once()


@pytest.mark.asyncio
async def test_generate_create():
    """Test generating create code from attributes and endpoints."""
    test_attributes = {
        "username": {"type": "string", "description": "User's login name"},
        "email": {"type": "string", "format": "email", "description": "Email address"},
    }

    test_endpoints = {"endpoints": [{"method": "POST", "path": "/users", "description": "Create user"}]}
    test_docs = [{"uuid": "doc1", "content": "Test documentation"}]

    with (
        patch("src.modules.codegen.service.SessionManager") as mock_session_manager,
        patch("src.modules.codegen.service.CreateGenerator") as mock_create_generator_class,
    ):
        mock_session_manager.get_session_data.return_value = None

        # Mock the generator instance and its generate method (must be async)
        mock_generator_instance = mock_create_generator_class.return_value
        mock_generator_instance.generate = AsyncMock(return_value="mocked create code")

        result = await service.create_create(
            attributes=test_attributes,
            endpoints=test_endpoints,
            documentation_items=test_docs,
            documentation="joined docs",
            session_id="test-session",
            object_class="User",
            job_id="test-job-id",
        )

        assert isinstance(result, dict)
        assert "code" in result
        assert result["code"] == "mocked create code"

        # Verify generator was instantiated and generate method was called
        mock_create_generator_class.assert_called_once()
        mock_generator_instance.generate.assert_called_once()


@pytest.mark.asyncio
async def test_generate_update():
    """Test generating update code from attributes and endpoints."""
    test_attributes = {
        "username": {"type": "string", "description": "User's login name"},
        "email": {"type": "string", "format": "email", "description": "Email address"},
    }

    test_endpoints = {"endpoints": [{"method": "PUT", "path": "/users/{id}", "description": "Update user"}]}
    test_docs = [{"uuid": "doc1", "content": "Test documentation"}]

    with (
        patch("src.modules.codegen.service.SessionManager") as mock_session_manager,
        patch("src.modules.codegen.service.UpdateGenerator") as mock_update_generator_class,
    ):
        mock_session_manager.get_session_data.return_value = None

        # Mock the generator instance and its generate method (must be async)
        mock_generator_instance = mock_update_generator_class.return_value
        mock_generator_instance.generate = AsyncMock(return_value="mocked update code")

        result = await service.create_update(
            attributes=test_attributes,
            endpoints=test_endpoints,
            documentation_items=test_docs,
            documentation="joined docs",
            session_id="test-session",
            object_class="User",
            job_id="test-job-id",
        )

        assert isinstance(result, dict)
        assert "code" in result
        assert result["code"] == "mocked update code"

        # Verify generator was instantiated and generate method was called
        mock_update_generator_class.assert_called_once()
        mock_generator_instance.generate.assert_called_once()


@pytest.mark.asyncio
async def test_generate_delete():
    """Test generating delete code from attributes and endpoints."""
    test_attributes = {
        "id": {"type": "string", "format": "uuid", "description": "Unique ID"},
    }

    test_endpoints = {"endpoints": [{"method": "DELETE", "path": "/users/{id}", "description": "Delete user"}]}
    test_docs = [{"uuid": "doc1", "content": "Test documentation"}]

    with (
        patch("src.modules.codegen.service.SessionManager") as mock_session_manager,
        patch("src.modules.codegen.service.DeleteGenerator") as mock_delete_generator_class,
    ):
        mock_session_manager.get_session_data.return_value = None

        # Mock the generator instance and its generate method (must be async)
        mock_generator_instance = mock_delete_generator_class.return_value
        mock_generator_instance.generate = AsyncMock(return_value="mocked delete code")

        result = await service.create_delete(
            attributes=test_attributes,
            endpoints=test_endpoints,
            documentation_items=test_docs,
            documentation="joined docs",
            session_id="test-session",
            object_class="User",
            job_id="test-job-id",
        )

        assert isinstance(result, dict)
        assert "code" in result
        assert result["code"] == "mocked delete code"

        # Verify generator was instantiated and generate method was called
        mock_delete_generator_class.assert_called_once()
        mock_generator_instance.generate.assert_called_once()
