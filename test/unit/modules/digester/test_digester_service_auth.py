# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.modules.digester import service
from src.modules.digester.enums import AuthType
from src.modules.digester.schema import AuthInfo


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
        patch("src.modules.digester.service.run_doc_extractors_concurrently", new_callable=AsyncMock) as mock_parallel,
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
        patch("src.modules.digester.service.run_doc_extractors_concurrently", new_callable=AsyncMock) as mock_parallel,
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
