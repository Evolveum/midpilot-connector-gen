# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, call, patch
from uuid import uuid4

import pytest

from src.modules.digester import service
from src.modules.digester.enums import AuthType
from src.modules.digester.schema import AuthInfo, AuthResponse
from src.modules.digester.utils.criteria import DEFAULT_CRITERIA


# ==================== EXTRACT AUTH ====================
@pytest.mark.asyncio
async def test_extract_auth_success(mock_llm, mock_digester_update_job_progress):
    doc_uuid1 = str(uuid4())
    doc_uuid2 = str(uuid4())

    fake_doc_items = [
        {
            "uuid": doc_uuid1,
            "content": "OAuth2 authentication documentation",
            "summary": "OAuth2 setup",
            "@metadata": {"source": "auth_guide"},
        },
        {
            "uuid": doc_uuid2,
            "content": "API Key authentication documentation",
            "summary": "API Key usage",
            "@metadata": {"source": "api_spec"},
        },
    ]

    with (
        patch("src.modules.digester.service.deduplicate_and_sort_auth", new_callable=AsyncMock) as mock_dedupe,
        patch("src.modules.digester.service._run_doc_extractors_concurrently", new_callable=AsyncMock) as mock_parallel,
    ):
        mock_parallel.return_value = [
            (
                [
                    AuthInfo(
                        name="OAuth2",
                        type=AuthType.OAUTH2,
                        quirks="Supports authorization_code and client_credentials",
                    )
                ],
                True,
                doc_uuid1,
            ),
            (
                [AuthInfo(name="API Key", type=AuthType.API_KEY, quirks="Header: X-API-Key")],
                True,
                doc_uuid2,
            ),
        ]

        class FakeDedupedAuth:
            def model_dump(self, **kwargs):
                return {
                    "auth": [
                        {
                            "name": "OAuth2",
                            "type": "oauth2",
                            "quirks": "Supports authorization_code and client_credentials",
                        },
                        {"name": "API Key", "type": "apiKey", "quirks": "Header: X-API-Key"},
                    ]
                }

        mock_dedupe.return_value = FakeDedupedAuth()

        job_id = uuid4()
        result = await service.extract_auth(fake_doc_items, job_id)

        assert "result" in result
        assert "relevantDocumentations" in result
        assert "auth" in result["result"]
        assert len(result["result"]["auth"]) == 2
        assert result["result"]["auth"][0]["name"] == "OAuth2"
        assert result["result"]["auth"][1]["name"] == "API Key"

        mock_parallel.assert_awaited_once()
        mock_dedupe.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_auth_empty_result(mock_llm, mock_digester_update_job_progress):
    """Test extract_auth when no authentication methods are found."""
    doc_uuid = str(uuid4())
    fake_doc_items = [{"uuid": doc_uuid, "content": "General documentation", "summary": "", "@metadata": {}}]

    with (
        patch("src.modules.digester.service.deduplicate_and_sort_auth", new_callable=AsyncMock) as mock_dedupe,
        patch("src.modules.digester.service._run_doc_extractors_concurrently", new_callable=AsyncMock) as mock_parallel,
    ):
        mock_parallel.return_value = [([], False, doc_uuid)]

        class EmptyAuth:
            def model_dump(self, **kwargs):
                return {"auth": []}

        mock_dedupe.return_value = EmptyAuth()

        result = await service.extract_auth(fake_doc_items, uuid4())

        assert result["result"]["auth"] == []
        mock_parallel.assert_awaited_once()
        mock_dedupe.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_auth_with_fallback_retries_with_default_criteria():
    session_id = uuid4()
    job_id = uuid4()
    auth_doc_items = [{"chunkId": str(uuid4()), "docId": str(uuid4()), "content": "Auth docs"}]
    default_doc_items = [{"chunkId": str(uuid4()), "docId": str(uuid4()), "content": "General API docs"}]

    empty_primary = {"result": {"auth": []}, "relevantDocumentations": []}
    fallback_result = {
        "result": {"auth": [{"name": "OAuth2", "type": "oauth2", "quirks": ""}]},
        "relevantDocumentations": [{"doc_id": str(uuid4()), "chunk_id": str(uuid4())}],
    }

    with (
        patch("src.modules.digester.service.extract_auth", new_callable=AsyncMock) as mock_extract_auth,
        patch("src.modules.digester.service.filter_documentation_items", new_callable=AsyncMock) as mock_filter,
        patch("src.modules.digester.service.update_job_progress", new_callable=AsyncMock) as mock_progress,
    ):
        mock_extract_auth.side_effect = [empty_primary, fallback_result]
        mock_filter.return_value = default_doc_items

        result = await service.extract_auth_with_fallback(
            auth_doc_items,
            used_auth_criteria=True,
            session_id=session_id,
            job_id=job_id,
        )

    assert result == fallback_result
    mock_extract_auth.assert_has_awaits([call(auth_doc_items, job_id), call(default_doc_items, job_id)])
    mock_filter.assert_awaited_once_with(DEFAULT_CRITERIA, session_id)
    mock_progress.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_auth_with_fallback_does_not_retry_when_primary_has_auth():
    session_id = uuid4()
    job_id = uuid4()
    auth_doc_items = [{"chunkId": str(uuid4()), "docId": str(uuid4()), "content": "Auth docs"}]
    primary_result = {
        "result": {"auth": [{"name": "API Key", "type": "apiKey", "quirks": ""}]},
        "relevantDocumentations": [],
    }

    with (
        patch("src.modules.digester.service.extract_auth", new_callable=AsyncMock) as mock_extract_auth,
        patch("src.modules.digester.service.filter_documentation_items", new_callable=AsyncMock) as mock_filter,
        patch("src.modules.digester.service.update_job_progress", new_callable=AsyncMock) as mock_progress,
    ):
        mock_extract_auth.return_value = primary_result

        result = await service.extract_auth_with_fallback(
            auth_doc_items,
            used_auth_criteria=True,
            session_id=session_id,
            job_id=job_id,
        )

    assert result == primary_result
    mock_extract_auth.assert_awaited_once_with(auth_doc_items, job_id)
    mock_filter.assert_not_awaited()
    mock_progress.assert_not_awaited()


@pytest.mark.asyncio
async def test_extract_auth_with_fallback_does_not_retry_when_auth_criteria_not_used():
    session_id = uuid4()
    job_id = uuid4()
    doc_items = [{"chunkId": str(uuid4()), "docId": str(uuid4()), "content": "General docs"}]
    empty_result = {"result": {"auth": []}, "relevantDocumentations": []}

    with (
        patch("src.modules.digester.service.extract_auth", new_callable=AsyncMock) as mock_extract_auth,
        patch("src.modules.digester.service.filter_documentation_items", new_callable=AsyncMock) as mock_filter,
        patch("src.modules.digester.service.update_job_progress", new_callable=AsyncMock) as mock_progress,
    ):
        mock_extract_auth.return_value = empty_result

        result = await service.extract_auth_with_fallback(
            doc_items,
            used_auth_criteria=False,
            session_id=session_id,
            job_id=job_id,
        )

    assert result == empty_result
    mock_extract_auth.assert_awaited_once_with(doc_items, job_id)
    mock_filter.assert_not_awaited()
    mock_progress.assert_not_awaited()


@pytest.mark.asyncio
async def test_extract_auth_with_fallback_switches_to_default_docs_without_real_llm():
    session_id = uuid4()
    job_id = uuid4()
    auth_chunk_id = str(uuid4())
    auth_doc_id = str(uuid4())
    default_chunk_id = str(uuid4())
    default_doc_id = str(uuid4())

    auth_doc_items = [{"chunkId": auth_chunk_id, "docId": auth_doc_id, "content": "Auth-filtered docs"}]
    default_doc_items = [{"chunkId": default_chunk_id, "docId": default_doc_id, "content": "Default-filtered docs"}]

    async def parallel_side_effect(*, chunk_items, job_id, extractor, logger_scope):
        if chunk_items == auth_doc_items:
            return [([], False, auth_chunk_id)]
        if chunk_items == default_doc_items:
            return [([AuthInfo(name="OAuth2", type=AuthType.OAUTH2, quirks="")], True, default_chunk_id)]
        return []

    async def dedupe_side_effect(auth_info, job_id):
        return AuthResponse(auth=auth_info)

    with (
        patch(
            "src.modules.digester.service._run_doc_extractors_concurrently",
            new_callable=AsyncMock,
            side_effect=parallel_side_effect,
        ) as mock_parallel,
        patch(
            "src.modules.digester.service.deduplicate_and_sort_auth",
            new_callable=AsyncMock,
            side_effect=dedupe_side_effect,
        ),
        patch("src.modules.digester.service.filter_documentation_items", new_callable=AsyncMock) as mock_filter,
        patch("src.modules.digester.service.update_job_progress", new_callable=AsyncMock) as mock_progress,
    ):
        mock_filter.return_value = default_doc_items

        result = await service.extract_auth_with_fallback(
            auth_doc_items,
            used_auth_criteria=True,
            session_id=session_id,
            job_id=job_id,
        )

    assert result["result"]["auth"] == [{"name": "OAuth2", "type": "oauth2", "quirks": ""}]
    assert result["relevantDocumentations"] == [{"doc_id": default_doc_id, "chunk_id": default_chunk_id}]
    mock_filter.assert_awaited_once_with(DEFAULT_CRITERIA, session_id)
    assert mock_parallel.await_count == 2
    mock_progress.assert_awaited()
