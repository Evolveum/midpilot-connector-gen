# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch

import pytest

from src.common.enums import ApiType, ScimAvailability
from src.modules.digester.extractors.apitype.knowledge import lookup_api_type_knowledge
from src.modules.digester.schemas import ApiTypeSignalResult


@pytest.mark.asyncio
async def test_lookup_returns_supported_scim_from_llm():
    response = ApiTypeSignalResult(supports_scim=True, api_type=[ApiType.SCIM, ApiType.REST])
    with patch(
        "src.modules.digester.extractors.apitype.knowledge.invoke_llm",
        new_callable=AsyncMock,
        return_value=response,
    ) as mock_invoke:
        result = await lookup_api_type_knowledge("Slack")

    assert result.supports_scim is True
    assert ApiType.SCIM in result.api_type
    mock_invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_lookup_surfaces_paid_availability():
    # Validate from a raw dict (the realistic LLM->pydantic path) so the "enterprise"
    # string exercises the scimAvailability normalizer.
    response = ApiTypeSignalResult.model_validate(
        {
            "supportsScim": True,
            "apiType": ["scim"],
            "scimAvailability": "enterprise",
            "requiredPlan": "Enterprise Grid",
        }
    )
    with patch(
        "src.modules.digester.extractors.apitype.knowledge.invoke_llm",
        new_callable=AsyncMock,
        return_value=response,
    ):
        result = await lookup_api_type_knowledge("Slack")

    # "enterprise" is normalized to the paid availability state.
    assert result.scim_availability is ScimAvailability.PAID
    assert result.required_plan == "Enterprise Grid"


def test_knowledge_response_defaults_availability_unknown():
    response = ApiTypeSignalResult(supports_scim=False)
    assert response.scim_availability is ScimAvailability.UNKNOWN
    assert response.required_plan == ""


def test_knowledge_response_unrecognized_availability_falls_back_to_unknown():
    response = ApiTypeSignalResult.model_validate({"scimAvailability": "something-weird"})
    assert response.scim_availability is ScimAvailability.UNKNOWN


@pytest.mark.asyncio
async def test_lookup_empty_name_skips_llm():
    with patch("src.modules.digester.extractors.apitype.knowledge.invoke_llm", new_callable=AsyncMock) as mock_invoke:
        result = await lookup_api_type_knowledge("   ")

    assert result.supports_scim is False
    mock_invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_lookup_disabled_skips_llm():
    with (
        patch("src.modules.digester.extractors.apitype.knowledge.config") as mock_config,
        patch("src.modules.digester.extractors.apitype.knowledge.invoke_llm", new_callable=AsyncMock) as mock_invoke,
    ):
        mock_config.digester.apitype_knowledge_enabled = False
        result = await lookup_api_type_knowledge("Slack")

    assert result.supports_scim is False
    mock_invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_lookup_llm_failure_is_graceful():
    with patch(
        "src.modules.digester.extractors.apitype.knowledge.invoke_llm",
        new_callable=AsyncMock,
        side_effect=RuntimeError("llm down"),
    ):
        result = await lookup_api_type_knowledge("Slack")

    assert result.supports_scim is False
    assert result.api_type == []
