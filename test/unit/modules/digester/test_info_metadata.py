"""Unit tests for digester info metadata normalization and accumulation."""

# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.modules.digester.extractors.info import extract_info_metadata
from src.modules.digester.schema import InfoMetadata


def test_info_metadata_base_api_endpoint_normalizes_single_object():
    metadata = InfoMetadata.model_validate(
        {
            "baseApiEndpoint": {
                "uri": "https://api.example.com/v1/",
                "type": "constant",
            }
        }
    )

    payload = metadata.model_dump(by_alias=True)
    assert payload["baseApiEndpoint"] == [{"uri": "https://api.example.com/v1/", "type": "constant"}]


def test_info_metadata_base_api_endpoint_deduplicates_and_sorts():
    metadata = InfoMetadata.model_validate(
        {
            "baseApiEndpoint": [
                {"uri": "https://b.example.com/api/v1/", "type": "dynamic"},
                {"uri": "https://A.example.com/api/v1/", "type": "dynamic"},
                {"uri": "https://b.example.com/api/v1/", "type": "constant"},
                {"uri": "https://b.example.com/api/v1/", "type": "dynamic"},
            ]
        }
    )

    payload = metadata.model_dump(by_alias=True)
    assert payload["baseApiEndpoint"] == [
        {"uri": "https://A.example.com/api/v1/", "type": "dynamic"},
        {"uri": "https://b.example.com/api/v1/", "type": "constant"},
        {"uri": "https://b.example.com/api/v1/", "type": "dynamic"},
    ]


@pytest.mark.asyncio
async def test_extract_info_metadata_keeps_prior_base_api_endpoints():
    chain = MagicMock()
    chain.ainvoke = AsyncMock(
        return_value={
            "infoAboutSchema": {
                "baseApiEndpoint": [
                    {"uri": "https://b.example.com/api/v2/", "type": "dynamic"},
                ]
            }
        }
    )

    initial_aggregated = {
        "infoAboutSchema": {
            "baseApiEndpoint": [
                {"uri": "https://a.example.com/api/v1/", "type": "dynamic"},
            ]
        }
    }

    with (
        patch("src.modules.digester.extractors.info.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.extractors.info.get_default_llm", return_value=MagicMock()),
        patch("src.modules.digester.extractors.info.make_basic_chain", return_value=chain),
        patch("src.modules.digester.extractors.info.extract_summary_and_tags", return_value=("", "")),
    ):
        result, has_relevant_data = await extract_info_metadata(
            schema="fake doc content",
            job_id=uuid4(),
            doc_id=uuid4(),
            initial_aggregated=initial_aggregated,
            doc_metadata={},
        )

    assert has_relevant_data is True
    assert result.model_dump(by_alias=True)["infoAboutSchema"]["baseApiEndpoint"] == [
        {"uri": "https://a.example.com/api/v1/", "type": "dynamic"},
        {"uri": "https://b.example.com/api/v2/", "type": "dynamic"},
    ]
