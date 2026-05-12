# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.modules.codegen import service


@pytest.mark.asyncio
async def test_create_authorization_uses_preferred_authorizations_and_auth_relevant_chunks():
    session_id = uuid4()
    job_id = uuid4()
    auth_payload = {
        "auth": [
            {
                "name": "Bearer token",
                "type": "bearer",
                "quirks": "Use Authorization header.",
                "relevant_sequences": [{"chunk_id": "chunk-bearer"}],
            },
            {
                "name": "Basic authentication",
                "type": "basic",
                "quirks": "Use username and password.",
                "relevant_sequences": [{"chunk_id": "chunk-basic"}],
            },
        ]
    }
    preferred_authorizations = [{"name": "Bearer token"}]
    enriched_preferred_authorizations = [
        {
            "name": "Bearer token",
            "type": "bearer",
            "quirks": "Use Authorization header.",
        }
    ]
    relevant_map = {
        "authOutput": [
            {"docId": "doc-1", "chunkId": "chunk-bearer"},
            {"docId": "doc-2", "chunkId": "chunk-basic"},
        ]
    }

    with (
        patch("src.modules.codegen.service.async_session_maker") as mock_session_maker,
        patch("src.modules.codegen.service.SessionRepository") as mock_session_repository,
        patch("src.modules.codegen.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
        patch("src.modules.codegen.service.get_session_base_api_url", new_callable=AsyncMock, return_value=""),
        patch("src.modules.codegen.service.AuthorizationGenerator") as mock_generator_class,
    ):
        mock_db_cm = mock_session_maker.return_value
        mock_db = AsyncMock()
        mock_db_cm.__aenter__.return_value = mock_db

        mock_repo_instance = mock_session_repository.return_value
        mock_repo_instance.get_session_data = AsyncMock(return_value=relevant_map)

        mock_generator_instance = mock_generator_class.return_value
        mock_generator_instance.generate = AsyncMock(return_value="mocked authorization code")

        result = await service.create_authorization(
            auth_payload=auth_payload,
            preferred_authorizations=preferred_authorizations,
            session_id=session_id,
            job_id=job_id,
        )

    assert result == {"code": "mocked authorization code"}
    mock_generator_class.assert_called_once()
    _, generator_kwargs = mock_generator_class.call_args
    assert generator_kwargs["preferred_authorizations"] == enriched_preferred_authorizations

    mock_generator_instance.generate.assert_awaited_once()
    _, generate_kwargs = mock_generator_instance.generate.call_args
    assert generate_kwargs["auth_payload"] == auth_payload
    assert generate_kwargs["relevant_chunk_pairs"] == [{"chunk_id": "chunk-bearer", "doc_id": "doc-1"}]
