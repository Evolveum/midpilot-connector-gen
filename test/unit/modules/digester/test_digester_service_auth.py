# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.modules.digester import service
from src.modules.digester.enums import AuthType
from src.modules.digester.extractors.auth import build_auth_items, deduplicate_auth
from src.modules.digester.schemas import (
    AuthDedupResponse,
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
        patch("src.modules.digester.service.run_doc_extractors_concurrently", new_callable=AsyncMock) as mock_parallel,
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
                        name="OAuth2 client credentials",
                        type=AuthType.OAUTH2_CLIENT_CREDENTIALS,
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
                name="OAuth2 client credentials",
                type=AuthType.OAUTH2_CLIENT_CREDENTIALS,
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
                name="OAuth2 client credentials",
                type=AuthType.OAUTH2_CLIENT_CREDENTIALS,
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

        sorted_result = AuthResponse[AuthInfo](
            auth=[
                AuthInfo(
                    name="OAuth2 client credentials",
                    type=AuthType.OAUTH2_CLIENT_CREDENTIALS,
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
        assert result["result"]["auth"][0]["name"] == "OAuth2 client credentials"
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
        patch("src.modules.digester.service.run_doc_extractors_concurrently", new_callable=AsyncMock) as mock_parallel,
    ):
        mock_parallel.return_value = [([], False, doc_uuid)]

        mock_deduplicate.side_effect = [[], []]
        mock_build.return_value = []
        mock_sort.return_value = AuthResponse[AuthInfo](auth=[])

        job_id = uuid4()
        result = await service.extract_auth(fake_doc_items, job_id)

        assert result["result"]["auth"] == []
        mock_parallel.assert_awaited_once()
        assert mock_deduplicate.await_count == 2
        mock_build.assert_awaited_once_with([], job_id)
        mock_sort.assert_awaited_once_with([], job_id)


@pytest.mark.asyncio
async def test_build_auth_items_filters_failed_build_results():
    valid_item = AuthProcessingInfo(
        name="API Key",
        type=AuthType.API_KEY,
        quirks="Header token",
        relevant_sequences=[],
    )

    with (
        patch("src.modules.digester.extractors.auth.update_job_progress", new_callable=AsyncMock),
        patch(
            "src.modules.digester.extractors.auth.run_all_items_build_parallel",
            new_callable=AsyncMock,
            return_value=[valid_item, None, [], object()],
        ),
    ):
        result = await build_auth_items([valid_item], uuid4())

    assert result == [valid_item]


@pytest.mark.asyncio
async def test_deduplicate_auth_keeps_distinct_methods_with_same_concrete_type():
    """Entries with the same concrete type can still represent different auth mechanisms."""
    items = [
        AuthProcessingInfo(
            name="X-API-Key header",
            type=AuthType.API_KEY,
            quirks="Send the key in the X-API-Key header.",
            relevant_sequences=[],
        ),
        AuthProcessingInfo(
            name="api_key query parameter",
            type=AuthType.API_KEY,
            quirks="Send the key in the api_key query parameter.",
            relevant_sequences=[],
        ),
    ]

    with (
        patch("src.modules.digester.extractors.auth.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.extractors.auth.build_structured_chain"),
        patch(
            "src.modules.digester.extractors.auth.invoke_llm",
            new_callable=AsyncMock,
            return_value=AuthDedupResponse(duplicates=[], to_be_deleted=[]),
        ),
    ):
        result = await deduplicate_auth(items, uuid4())

    assert {auth.name for auth in result} == {"X-API-Key header", "api_key query parameter"}


@pytest.mark.asyncio
async def test_deduplicate_auth_uses_normalized_type_for_name_matches():
    """Alias spellings of the same auth type should use the same comparison key."""
    items = [
        AuthProcessingInfo.model_construct(name="Token", type="token", quirks="", relevant_sequences=[]),
        AuthProcessingInfo.model_construct(name="Bearer token", type="bearer", quirks="", relevant_sequences=[]),
    ]

    with (
        patch("src.modules.digester.extractors.auth.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.extractors.auth.build_structured_chain"),
        patch(
            "src.modules.digester.extractors.auth.invoke_llm",
            new_callable=AsyncMock,
            return_value=AuthDedupResponse(duplicates=[], to_be_deleted=[]),
        ),
    ):
        result = await deduplicate_auth(items, uuid4())

    assert len(result) == 1
    assert result[0].name == "Bearer token"
    assert result[0].type == AuthType.BEARER


@pytest.mark.asyncio
async def test_deduplicate_auth_keeps_distinct_other_methods():
    """The 'other' bucket is a catch-all for distinct unknown flows, so entries with
    different names must NOT collapse the way concrete types do."""
    items = [
        AuthProcessingInfo(
            name="OAuth 2.0 Authorization Code Grant", type=AuthType.OTHER, quirks="", relevant_sequences=[]
        ),
        AuthProcessingInfo(name="Custom signed request", type=AuthType.OTHER, quirks="", relevant_sequences=[]),
    ]

    with (
        patch("src.modules.digester.extractors.auth.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.extractors.auth.build_structured_chain"),
        patch(
            "src.modules.digester.extractors.auth.invoke_llm",
            new_callable=AsyncMock,
            return_value=AuthDedupResponse(duplicates=[], to_be_deleted=[]),
        ),
    ):
        result = await deduplicate_auth(items, uuid4())

    assert {auth.name for auth in result} == {"OAuth 2.0 Authorization Code Grant", "Custom signed request"}


@pytest.mark.asyncio
async def test_deduplicate_auth_llm_pair_matches_normalized_names():
    """LLM dedup pairs must resolve against stored entries even when the names differ only
    by casing/spaces/dashes, using the same normalization key as the heuristic pass."""
    items = [
        AuthProcessingInfo(name="API Key", type=AuthType.API_KEY, quirks="", relevant_sequences=[]),
        AuthProcessingInfo(name="Legacy token", type=AuthType.BEARER, quirks="", relevant_sequences=[]),
    ]

    # The LLM spells the names differently than stored ("API-Key" vs "API Key",
    # "legacytoken" vs "Legacy token"); they must still match and merge.
    dedup = AuthDedupResponse(
        duplicates=[(("API-Key", "apiKey"), ("legacytoken", "bearer"))],
        to_be_deleted=[],
    )

    with (
        patch("src.modules.digester.extractors.auth.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.extractors.auth.build_structured_chain"),
        patch(
            "src.modules.digester.extractors.auth.invoke_llm",
            new_callable=AsyncMock,
            return_value=dedup,
        ),
    ):
        result = await deduplicate_auth(items, uuid4())

    assert [auth.name for auth in result] == ["API Key"]


def test_auth_response_serializes_relevant_sequences_in_camel_case():
    response = AuthResponse[AuthInfo](
        auth=[
            AuthInfo(
                name="OAuth2",
                type=AuthType.OAUTH2_CLIENT_CREDENTIALS,
                quirks="",
                relevant_sequences=[
                    DocSequenceItem(
                        chunk_id="chunk-1",
                        start_sequence="start marker",
                        end_sequence="end marker",
                    )
                ],
            )
        ]
    )

    dumped = response.model_dump(by_alias=True, mode="json")
    sequence = dumped["auth"][0]["relevantSequences"][0]
    assert sequence == {
        "chunkId": "chunk-1",
        "startSequence": "start marker",
        "endSequence": "end marker",
    }
