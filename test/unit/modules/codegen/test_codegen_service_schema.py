# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for codegen service schema generators."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

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
        patch("src.modules.codegen.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
    ):
        mock_generate_groovy.return_value = "mocked groovy code"

        result = await service.create_native_schema(
            test_attributes,
            "User",
            session_id=uuid4(),
            job_id=uuid4(),
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
            job_id=uuid4(),
        )

        assert isinstance(result, dict)
        assert "code" in result
        assert result["code"] == "mocked connid code"

        mock_generate_groovy.assert_called_once()
