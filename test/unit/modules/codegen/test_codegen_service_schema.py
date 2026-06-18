# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for codegen service schema generators."""

from unittest.mock import patch
from uuid import uuid4

import pytest

from src.common.enums import ApiType
from src.modules.codegen import service


@pytest.mark.asyncio
async def test_generate_native_schema():
    """Test generating native schema from attributes."""
    test_attributes = {
        "username": {"type": "string", "description": "User's login name", "mandatory": True},
        "id": {"type": "string", "format": "uuid", "description": "Unique identifier"},
    }

    with (
        patch("src.modules.codegen.service.generate_groovy") as mock_generate_groovy,
    ):
        mock_generate_groovy.return_value = "mocked groovy code"

        result = await service.create_native_schema(
            test_attributes,
            "User",
            session_id=uuid4(),
            job_id=uuid4(),
            protocol=ApiType.REST,
        )

        assert isinstance(result, dict)
        assert "code" in result
        assert result["code"] == "mocked groovy code"

        mock_generate_groovy.assert_called_once()


@pytest.mark.asyncio
async def test_generate_native_schema_uses_sql_docs_for_sql_api_type():
    test_attributes = {
        "username": {"type": "varchar", "description": "User login", "mandatory": True},
    }

    with (
        patch("src.modules.codegen.service.generate_groovy") as mock_generate_groovy,
    ):
        mock_generate_groovy.return_value = "mocked sql schema code"

        result = await service.create_native_schema(
            test_attributes,
            "User",
            session_id=uuid4(),
            job_id=uuid4(),
            protocol=ApiType.SQL,
        )

    assert result == {"code": "mocked sql schema code"}
    _, kwargs = mock_generate_groovy.call_args
    assert "SQL native schema mapping" in kwargs["extra_prompt_vars"]["user_schema_docs"]


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
            job_id=uuid4(),
        )

        assert isinstance(result, dict)
        assert "code" in result
        assert result["code"] == "mocked connid code"

        mock_generate_groovy.assert_called_once()
