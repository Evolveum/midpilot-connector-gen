# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Integration tests for codegen search endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.modules.codegen.router import generate_search


# SEARCH
@pytest.mark.asyncio
async def test_generate_search_success():
    """Test successful generation of search code."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.update_session = AsyncMock()

    attrs_payload = {"username": {"type": "string"}}
    endpoints_payload = {"endpoints": [{"method": "GET", "path": "/users"}]}

    async def fake_get_session_data(session_id, key):
        if key.endswith("AttributesOutput"):
            return attrs_payload
        if key.endswith("EndpointsOutput"):
            return endpoints_payload
        return None

    mock_repo.get_session_data = AsyncMock(side_effect=fake_get_session_data)

    with (
        patch("src.modules.codegen.router.SessionRepository", return_value=mock_repo),
        patch("src.modules.codegen.router.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
        patch("src.modules.codegen.router.get_session_api_types", new_callable=AsyncMock, return_value=[]),
    ):
        job_id = uuid4()
        session_id = uuid4()
        mock_schedule.return_value = job_id

        response = await generate_search(session_id, "User", "all", db=MagicMock())

    assert response.jobId == job_id
    mock_repo.session_exists.assert_awaited_once_with(session_id)
    assert mock_repo.get_session_data.await_count == 2
    mock_schedule.assert_awaited_once()
    mock_repo.update_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_search_scim_allows_missing_endpoints():
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    attrs_payload = {"username": {"type": "string"}}

    async def fake_get_session_data(session_id, key):
        if key.endswith("AttributesOutput"):
            return attrs_payload
        if key.endswith("EndpointsOutput"):
            return None
        return None

    mock_repo.get_session_data = AsyncMock(side_effect=fake_get_session_data)
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.codegen.router.SessionRepository", return_value=mock_repo),
        patch("src.modules.codegen.router.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
        patch("src.modules.codegen.router.get_session_api_types", new_callable=AsyncMock, return_value=["SCIM"]),
    ):
        job_id = uuid4()
        session_id = uuid4()
        mock_schedule.return_value = job_id

        response = await generate_search(session_id, "User", "all", db=MagicMock())

        assert response.jobId == job_id
        mock_repo.session_exists.assert_awaited_once_with(session_id)
        assert mock_repo.get_session_data.await_count == 2
        mock_schedule.assert_awaited_once()
        mock_repo.update_session.assert_awaited_once()

        update_args = mock_repo.update_session.call_args[0]
        assert update_args[0] == session_id
        inputs = update_args[1]
        assert inputs["UserSearchAllInput"] == {"objectClass": "User", "attributes": attrs_payload, "intent": "all"}
