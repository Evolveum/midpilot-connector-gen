# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Integration tests for codegen ConnId endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.modules.codegen.router import generate_connid


# CONNID
@pytest.mark.asyncio
async def test_generate_connid_success():
    """Test successful generation of ConnID code."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.get_session_data = AsyncMock(return_value={"username": {"type": "string"}})
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.codegen.router.SessionRepository", return_value=mock_repo),
        patch("src.modules.codegen.router.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
        patch("src.modules.codegen.router.get_session_api_types", new_callable=AsyncMock, return_value=[]),
    ):
        job_id = uuid4()
        session_id = uuid4()
        mock_schedule.return_value = job_id

        response = await generate_connid(session_id, "User", db=MagicMock())

        assert response.jobId == job_id
        mock_repo.session_exists.assert_awaited_once_with(session_id)
        mock_repo.get_session_data.assert_awaited_once_with(session_id, "UserAttributesOutput")
        mock_schedule.assert_awaited_once()
        mock_repo.update_session.assert_awaited_once()
