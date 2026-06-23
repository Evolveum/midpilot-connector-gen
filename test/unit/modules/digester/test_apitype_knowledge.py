# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch

import pytest

from src.common.enums import ApiType
from src.modules.digester.apitype.knowledge import lookup_api_type_knowledge
from src.modules.digester.schemas import ApiTypeKnowledgeResponse


@pytest.mark.asyncio
async def test_lookup_returns_supported_scim_from_llm():
    response = ApiTypeKnowledgeResponse(supports_scim=True, api_type=[ApiType.SCIM, ApiType.REST])
    with patch(
        "src.modules.digester.apitype.knowledge.invoke_llm",
        new_callable=AsyncMock,
        return_value=response,
    ) as mock_invoke:
        result = await lookup_api_type_knowledge("Slack")

    assert result.supports_scim is True
    assert ApiType.SCIM in result.api_type
    mock_invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_lookup_empty_name_skips_llm():
    with patch("src.modules.digester.apitype.knowledge.invoke_llm", new_callable=AsyncMock) as mock_invoke:
        result = await lookup_api_type_knowledge("   ")

    assert result.supports_scim is False
    mock_invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_lookup_disabled_skips_llm():
    with (
        patch("src.modules.digester.apitype.knowledge.config") as mock_config,
        patch("src.modules.digester.apitype.knowledge.invoke_llm", new_callable=AsyncMock) as mock_invoke,
    ):
        mock_config.digester.apitype_knowledge_enabled = False
        result = await lookup_api_type_knowledge("Slack")

    assert result.supports_scim is False
    mock_invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_lookup_llm_failure_is_graceful():
    with patch(
        "src.modules.digester.apitype.knowledge.invoke_llm",
        new_callable=AsyncMock,
        side_effect=RuntimeError("llm down"),
    ):
        result = await lookup_api_type_knowledge("Slack")

    assert result.supports_scim is False
    assert result.api_type == []
