# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.modules.digester import service
from src.modules.digester.enums import AuthType
from src.modules.digester.schema import (
    AuthInfo,
    AuthProcessingInfo,
    AuthResponse,
    DiscoveryAuth,
    DocProcessingSequenceItem,
    DocSequenceItem,
)


# ==================== EXTRACT AUTH ====================
@pytest.mark.asyncio
async def test_extract_auth_success(mock_llm, mock_digester_update_job_progress):
    doc_uuid1 = str(uuid4())
    doc_uuid2 = str(uuid4())

    fake_doc_items = [
        {
            "chunkId": doc_uuid1,
            "docId": str(uuid4()),
            "content": "OAuth2 authentication documentation",
            "summary": "OAuth2 setup",
            "@metadata": {"source": "auth_guide"},
        },
        {
            "chunkId": doc_uuid2,
            "docId": str(uuid4()),
            "content": "API Key authentication documentation",
            "summary": "API Key usage",
            "@metadata": {"source": "api_spec"},
        },
    ]

    with (
        patch("src.modules.digester.service.deduplicate_auth", new_callable=AsyncMock) as mock_deduplicate,
        patch("src.modules.digester.service.build_auth_items", new_callable=AsyncMock) as mock_build,
        patch("src.modules.digester.service.sort_auth_by_importance", new_callable=AsyncMock) as mock_sort,
        patch("src.modules.digester.service._run_doc_extractors_concurrently", new_callable=AsyncMock) as mock_parallel,
    ):
        oauth_doc_seq = DocSequenceItem(
            chunk_id=doc_uuid1,
            start_sequence="OAuth2 authentication",
            end_sequence="authorization_code",
        )
        api_key_doc_seq = DocSequenceItem(
            chunk_id=doc_uuid2,
            start_sequence="API Key authentication",
            end_sequence="X-API-Key",
        )

        mock_parallel.return_value = [
            (
                [
                    DiscoveryAuth(
                        name="OAuth2",
                        type=AuthType.OAUTH2,
                        relevant_sequences=[oauth_doc_seq],
                    )
                ],
                True,
                doc_uuid1,
            ),
            (
                [
                    DiscoveryAuth(
                        name="API Key",
                        type=AuthType.API_KEY,
                        relevant_sequences=[api_key_doc_seq],
                    )
                ],
                True,
                doc_uuid2,
            ),
        ]

        first_dedup_result = [
            AuthProcessingInfo(
                name="OAuth2",
                type=AuthType.OAUTH2,
                quirks="",
                relevant_sequences=[
                    DocProcessingSequenceItem(
                        chunk_id=doc_uuid1,
                        start_sequence="OAuth2 authentication",
                        end_sequence="authorization_code",
                        text="OAuth2 authentication supports authorization_code and client_credentials",
                    )
                ],
            ),
            AuthProcessingInfo(
                name="API Key",
                type=AuthType.API_KEY,
                quirks="",
                relevant_sequences=[
                    DocProcessingSequenceItem(
                        chunk_id=doc_uuid2,
                        start_sequence="API Key authentication",
                        end_sequence="X-API-Key",
                        text="API Key authentication uses X-API-Key header",
                    )
                ],
            ),
        ]

        built_result = [
            AuthProcessingInfo(
                name="OAuth2",
                type=AuthType.OAUTH2,
                quirks="Supports authorization_code and client_credentials",
                relevant_sequences=[
                    DocProcessingSequenceItem(
                        chunk_id=doc_uuid1,
                        start_sequence="OAuth2 authentication",
                        end_sequence="authorization_code",
                        text="OAuth2 authentication supports authorization_code and client_credentials",
                    )
                ],
            ),
            AuthProcessingInfo(
                name="API Key",
                type=AuthType.API_KEY,
                quirks="Header: X-API-Key",
                relevant_sequences=[
                    DocProcessingSequenceItem(
                        chunk_id=doc_uuid2,
                        start_sequence="API Key authentication",
                        end_sequence="X-API-Key",
                        text="API Key authentication uses X-API-Key header",
                    )
                ],
            ),
        ]

        sorted_result = AuthResponse(
            auth=[
                AuthInfo(
                    name="OAuth2",
                    type=AuthType.OAUTH2,
                    quirks="Supports authorization_code and client_credentials",
                    relevant_sequences=[oauth_doc_seq],
                ),
                AuthInfo(
                    name="API Key",
                    type=AuthType.API_KEY,
                    quirks="Header: X-API-Key",
                    relevant_sequences=[api_key_doc_seq],
                ),
            ]
        )

        mock_deduplicate.side_effect = [first_dedup_result, built_result]
        mock_build.return_value = built_result
        mock_sort.return_value = sorted_result

        job_id = uuid4()
        result = await service.extract_auth(fake_doc_items, job_id)

        assert "result" in result
        assert "relevantDocumentations" in result
        assert "auth" in result["result"]
        assert len(result["result"]["auth"]) == 2
        assert result["result"]["auth"][0]["name"] == "OAuth2"
        assert result["result"]["auth"][1]["name"] == "API Key"

        mock_parallel.assert_awaited_once()
    assert mock_deduplicate.await_count == 2
    mock_build.assert_awaited_once_with(first_dedup_result, job_id)
    mock_sort.assert_awaited_once_with(built_result, job_id)


@pytest.mark.asyncio
async def test_extract_auth_empty_result(mock_llm, mock_digester_update_job_progress):
    """Test extract_auth when no authentication methods are found."""
    doc_uuid = str(uuid4())
    fake_doc_items = [
        {"chunkId": doc_uuid, "docId": str(uuid4()), "content": "General documentation", "summary": "", "@metadata": {}}
    ]

    with (
        patch("src.modules.digester.service.deduplicate_auth", new_callable=AsyncMock) as mock_deduplicate,
        patch("src.modules.digester.service.build_auth_items", new_callable=AsyncMock) as mock_build,
        patch("src.modules.digester.service.sort_auth_by_importance", new_callable=AsyncMock) as mock_sort,
        patch("src.modules.digester.service._run_doc_extractors_concurrently", new_callable=AsyncMock) as mock_parallel,
    ):
        mock_parallel.return_value = [([], False, doc_uuid)]

        mock_deduplicate.side_effect = [[], []]
        mock_build.return_value = []
        mock_sort.return_value = AuthResponse(auth=[])

        job_id = uuid4()
        result = await service.extract_auth(fake_doc_items, job_id)

        assert result["result"]["auth"] == []
        mock_parallel.assert_awaited_once()
        assert mock_deduplicate.await_count == 2
        mock_build.assert_awaited_once_with([], job_id)
        mock_sort.assert_awaited_once_with([], job_id)
