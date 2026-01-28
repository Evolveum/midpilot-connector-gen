# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.


from unittest.mock import ANY, AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.common.enums import JobStatus
from src.modules.codegen.router import (
    generate_connid,
    generate_native_schema,
    generate_relation_code,
    generate_search,
    get_native_schema_status,
    get_relation_code_status,
    override_native_schema,
)


# NATIVE SCHEMA
@pytest.mark.asyncio
async def test_generate_native_schema_success():
    """Test successful generation of native schema."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.get_session_data = AsyncMock(return_value={"username": {"type": "string"}})
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.codegen.router.SessionRepository", return_value=mock_repo),
        patch("src.modules.codegen.router.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        job_id = uuid4()
        session_id = uuid4()
        mock_schedule.return_value = job_id

        response = await generate_native_schema(session_id, "User", db=MagicMock())

        assert response.jobId == job_id
        mock_repo.session_exists.assert_awaited_once_with(session_id)
        mock_repo.get_session_data.assert_awaited_once_with(session_id, "UserAttributesOutput")
        mock_schedule.assert_awaited_once_with(
            job_type="codegen.getNativeSchema",
            input_payload={"attributes": {"username": {"type": "string"}}, "objectClass": "User"},
            worker=ANY,
            worker_args=({"username": {"type": "string"}}, "User"),
            initial_stage="queue",
            initial_message="Queued code generation",
            session_id=session_id,
            session_result_key="UserNativeSchema",
        )
        mock_repo.update_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_native_schema_status_found():
    """Test getting native schema generation status when job exists."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)

    fake_status = MagicMock(
        jobId=ANY,
        status=JobStatus.finished,
        result={"code": "mocked groovy code"},
        progress={"stage": "queued"},
        errors=None,
    )

    with (
        patch("src.modules.codegen.router.SessionRepository", return_value=mock_repo),
        patch(
            "src.modules.codegen.router.build_stage_status_response", new_callable=AsyncMock, return_value=fake_status
        ) as mock_builder,
    ):
        job_id = uuid4()
        session_id = uuid4()

        response = await get_native_schema_status(session_id, "User", job_id, db=MagicMock())

        assert response.status == JobStatus.finished
        assert response.result == {"code": "mocked groovy code"}
        mock_repo.session_exists.assert_awaited_once_with(session_id)
        mock_builder.assert_awaited_once_with(job_id)


@pytest.mark.asyncio
async def test_override_native_schema_success():
    """Test manual override of native schema."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.update_session = AsyncMock()

    with patch("src.modules.codegen.router.SessionRepository", return_value=mock_repo):
        session_id = uuid4()
        response = await override_native_schema(
            session_id,
            "User",
            {"code": "custom groovy code"},
            db=MagicMock(),
        )

        assert response["message"] == "Native schema for User overridden successfully"
        assert response["sessionId"] == session_id
        assert response["objectClass"] == "User"
        mock_repo.update_session.assert_awaited_once_with(
            session_id,
            {"UserNativeSchema": {"code": "custom groovy code"}},
        )


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
    ):
        job_id = uuid4()
        session_id = uuid4()
        mock_schedule.return_value = job_id

        response = await generate_search(session_id, "User", "GET", db=MagicMock())

        assert response.jobId == job_id
        mock_repo.session_exists.assert_awaited_once_with(session_id)
        assert mock_repo.get_session_data.await_count == 2
        mock_schedule.assert_awaited_once()
        mock_repo.update_session.assert_awaited_once()


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
            "src.modules.codegen.router._build_multi_doc_status_response",
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


# ERROR HANDLING
@pytest.mark.asyncio
async def test_generate_native_schema_missing_class():
    """If attributes for the class don't exist in session, raise 404."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.get_session_data = AsyncMock(return_value=None)

    with patch("src.modules.codegen.router.SessionRepository", return_value=mock_repo):
        with pytest.raises(HTTPException) as exc_info:
            await generate_native_schema(uuid4(), "NonExistentClass", db=MagicMock())

    assert exc_info.value.status_code == 404
    assert "No attributes found for NonExistentClass" in exc_info.value.detail
