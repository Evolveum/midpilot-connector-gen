# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Integration tests for codegen relation endpoint."""

from unittest.mock import ANY, AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.common.enums import JobStatus
from src.modules.codegen.router import generate_relation_code, get_relation_code_status


# RELATION
@pytest.mark.asyncio
async def test_generate_relation_code_success():
    """Test successful generation of relation code."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    relations_payload = {
        "relations": [
            {
                "subject": "User",
                "object": "Group",
                "subjectAttribute": "members",
                "objectAttribute": "",
                "shortDescription": "",
                "name": "User to Group",
            }
        ]
    }
    mock_repo.get_session_data = AsyncMock(return_value=relations_payload)
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.codegen.router.SessionRepository", return_value=mock_repo),
        patch("src.modules.codegen.router.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
        patch("src.modules.codegen.router.get_session_api_types", new_callable=AsyncMock, return_value=[]),
    ):
        job_id = uuid4()
        session_id = uuid4()
        mock_schedule.return_value = job_id

        response = await generate_relation_code(session_id, "membership", db=MagicMock())

        assert response.jobId == job_id
        mock_repo.session_exists.assert_awaited_once_with(session_id)
        mock_repo.get_session_data.assert_awaited_once_with(session_id, "relationsOutput")
        mock_schedule.assert_awaited_once()
        mock_repo.update_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_relation_code_status_found():
    """Test getting relation code generation status when job exists."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)

    fake_status = MagicMock(
        jobId=ANY,
        status=JobStatus.finished,
        result="mocked relation code",
        progress=None,
        errors=None,
    )

    with (
        patch("src.modules.codegen.router.SessionRepository", return_value=mock_repo),
        patch(
            "src.modules.codegen.router.build_multi_doc_status_response",
            new_callable=AsyncMock,
            return_value=fake_status,
        ) as mock_builder,
    ):
        job_id = uuid4()
        session_id = uuid4()

        response = await get_relation_code_status(session_id, "membership", job_id, db=MagicMock())

        assert response.status == JobStatus.finished
        assert response.result == "mocked relation code"
        mock_repo.session_exists.assert_awaited_once_with(session_id)
        mock_builder.assert_awaited_once_with(job_id)
