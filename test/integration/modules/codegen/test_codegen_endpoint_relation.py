# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Integration tests for codegen relation endpoint."""

from unittest.mock import ANY, AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

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
                "name": "user_to_group",
                "displayName": "User to Group",
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

        response = await generate_relation_code(session_id, "user_to_group", db=MagicMock())

        assert response.jobId == job_id
        mock_repo.session_exists.assert_awaited_once_with(session_id)
        mock_repo.get_session_data.assert_awaited_once_with(session_id, "relationsOutput")
        mock_schedule.assert_awaited_once()
        schedule_kwargs = mock_schedule.await_args.kwargs
        assert schedule_kwargs["input_payload"]["relationName"] == "user_to_group"
        assert [item["name"] for item in schedule_kwargs["input_payload"]["relations"]["relations"]] == [
            "user_to_group"
        ]
        assert schedule_kwargs["worker_kwargs"]["relations"].relations[0].name == "user_to_group"
        assert schedule_kwargs["worker_kwargs"]["relation_name"] == "user_to_group"
        mock_repo.update_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_relation_code_selects_relation_by_name():
    """Only the requested relation should be passed to code generation."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    relations_payload = {
        "relations": [
            {
                "subject": "principal",
                "object": "role",
                "subjectAttribute": "roles",
                "objectAttribute": "",
                "shortDescription": "Principal receives a role.",
                "name": "principal_to_role",
                "displayName": "Principal to Role",
            },
            {
                "subject": "principal",
                "object": "membership",
                "subjectAttribute": "memberships",
                "objectAttribute": "",
                "shortDescription": "Principal has memberships.",
                "name": "principal_to_membership",
                "displayName": "Principal to Membership",
            },
        ]
    }
    mock_repo.get_session_data = AsyncMock(return_value=relations_payload)
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.codegen.router.SessionRepository", return_value=mock_repo),
        patch("src.modules.codegen.router.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        job_id = uuid4()
        session_id = uuid4()
        mock_schedule.return_value = job_id

        response = await generate_relation_code(session_id, "principal_to_membership", db=MagicMock())

    assert response.jobId == job_id
    schedule_kwargs = mock_schedule.await_args.kwargs
    assert schedule_kwargs["input_payload"]["relations"] == {
        "relations": [
            {
                "name": "principal_to_membership",
                "displayName": "Principal to Membership",
                "shortDescription": "Principal has memberships.",
                "subject": "principal",
                "subjectAttribute": "memberships",
                "object": "membership",
                "objectAttribute": "",
            }
        ]
    }
    assert len(schedule_kwargs["worker_kwargs"]["relations"].relations) == 1
    assert schedule_kwargs["worker_kwargs"]["relations"].relations[0].name == "principal_to_membership"


@pytest.mark.asyncio
async def test_generate_relation_code_rejects_missing_display_name():
    """Relation codegen should require the current RelationsResponse format."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    relations_payload = {
        "relations": [
            {
                "subject": "user",
                "object": "group",
                "subjectAttribute": "groups",
                "objectAttribute": "",
                "shortDescription": "",
                "name": "user_to_group",
            }
        ]
    }
    mock_repo.get_session_data = AsyncMock(return_value=relations_payload)
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.codegen.router.SessionRepository", return_value=mock_repo),
        patch("src.modules.codegen.router.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        session_id = uuid4()

        with pytest.raises(HTTPException) as exc_info:
            await generate_relation_code(session_id, "user_to_group", db=MagicMock())

    assert exc_info.value.status_code == 422
    assert "Stored relationsOutput is invalid" in exc_info.value.detail["message"]
    mock_schedule.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_relation_code_rejects_unknown_relation_name():
    """Relation codegen should fail before scheduling when the route name is absent."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    relations_payload = {
        "relations": [
            {
                "subject": "user",
                "object": "group",
                "subjectAttribute": "groups",
                "objectAttribute": "",
                "shortDescription": "",
                "name": "user_to_group",
                "displayName": "User to Group",
            }
        ]
    }
    mock_repo.get_session_data = AsyncMock(return_value=relations_payload)
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.codegen.router.SessionRepository", return_value=mock_repo),
        patch("src.modules.codegen.router.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        session_id = uuid4()

        with pytest.raises(HTTPException) as exc_info:
            await generate_relation_code(session_id, "principal_to_role", db=MagicMock())

    assert exc_info.value.status_code == 404
    assert "Relation principal_to_role not found" in exc_info.value.detail
    mock_schedule.assert_not_awaited()


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
