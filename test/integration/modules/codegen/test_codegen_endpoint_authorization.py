# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Integration tests for codegen authorization endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.common.enums import ApiType
from src.modules.codegen.router import generate_authorization, get_authorization_status, override_authorization
from src.modules.codegen.schema import AuthorizationCodegenInput, GroovyCodePayload


@pytest.mark.asyncio
async def test_generate_authorization_includes_preferred_authorizations_in_job_and_session_input():
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.update_session = AsyncMock()

    auth_payload = {
        "auth": [
            {
                "name": "Bearer token",
                "type": "bearer",
                "quirks": "Use Authorization header.",
                "relevant_sequences": [{"chunk_id": "chunk-1", "start_sequence": "Bearer", "end_sequence": "token"}],
            },
            {
                "name": "Basic authentication",
                "type": "basic",
                "quirks": "Use username and password.",
                "relevant_sequences": [],
            },
        ]
    }
    preferred_authorizations = [
        {"name": "Bearer token", "type": "bearer"},
        {"name": "Basic authentication", "type": "basic"},
    ]
    enriched_preferred_authorizations = [
        {
            "name": "Bearer token",
            "type": "bearer",
            "quirks": "Use Authorization header.",
        },
        {
            "name": "Basic authentication",
            "type": "basic",
            "quirks": "Use username and password.",
        },
    ]

    mock_repo.get_session_data = AsyncMock(return_value=auth_payload)

    with (
        patch("src.modules.codegen.router.SessionRepository", return_value=mock_repo),
        patch("src.modules.codegen.router.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
        patch(
            "src.modules.codegen.router.resolve_effective_api_type",
            new_callable=AsyncMock,
            return_value=ApiType.REST,
        ),
    ):
        job_id = uuid4()
        session_id = uuid4()
        mock_schedule.return_value = job_id

        response = await generate_authorization(
            session_id=session_id,
            skip_cache=False,
            db=MagicMock(),
            codegen_input=AuthorizationCodegenInput.model_validate(
                {"preferred_authorizations": preferred_authorizations}
            ),
        )

    assert response.jobId == job_id
    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_repo.get_session_data.assert_awaited_once_with(session_id, "authOutput")

    _, schedule_kwargs = mock_schedule.call_args
    assert schedule_kwargs["job_type"] == "codegen.getAuthorization"
    assert schedule_kwargs["input_payload"]["preferredAuthorizations"] == enriched_preferred_authorizations
    assert schedule_kwargs["worker_kwargs"]["preferred_authorizations"] == enriched_preferred_authorizations
    assert schedule_kwargs["session_result_key"] == "authorizationOutput"

    update_args = mock_repo.update_session.call_args[0]
    inputs = update_args[1]
    assert inputs["authorizationJobId"] == str(job_id)
    assert inputs["authorizationInput"]["preferredAuthorizations"] == enriched_preferred_authorizations


@pytest.mark.asyncio
async def test_generate_authorization_allows_midpoint_authorization_when_auth_output_is_missing():
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.get_session_data = AsyncMock(return_value=None)
    mock_repo.update_session = AsyncMock()

    preferred_authorizations = [
        {
            "name": "HTTP JWT Bearer Token Authorization",
            "type": "jwtBearer",
            "quirks": "",
        }
    ]
    expected_preferred_authorizations = [
        {
            "name": "HTTP JWT Bearer Token Authorization",
            "type": "jwtBearer",
        }
    ]

    with (
        patch("src.modules.codegen.router.SessionRepository", return_value=mock_repo),
        patch("src.modules.codegen.router.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
        patch(
            "src.modules.codegen.router.resolve_effective_api_type",
            new_callable=AsyncMock,
            return_value=ApiType.REST,
        ),
    ):
        job_id = uuid4()
        session_id = uuid4()
        mock_schedule.return_value = job_id

        response = await generate_authorization(
            session_id=session_id,
            skip_cache=False,
            db=MagicMock(),
            codegen_input=AuthorizationCodegenInput.model_validate(
                {"preferred_authorizations": preferred_authorizations}
            ),
        )

    assert response.jobId == job_id
    _, schedule_kwargs = mock_schedule.call_args
    assert schedule_kwargs["input_payload"]["auth"] == {"auth": []}
    assert schedule_kwargs["input_payload"]["preferredAuthorizations"] == expected_preferred_authorizations
    assert schedule_kwargs["worker_kwargs"]["preferred_authorizations"] == expected_preferred_authorizations


@pytest.mark.asyncio
async def test_get_authorization_status_found():
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    job_id = uuid4()
    mock_repo.get_session_data = AsyncMock(return_value=str(job_id))

    fake_status = MagicMock(jobId=job_id)

    with (
        patch("src.modules.codegen.router.SessionRepository", return_value=mock_repo),
        patch(
            "src.modules.codegen.router.build_multi_doc_status_response",
            new_callable=AsyncMock,
            return_value=fake_status,
        ) as mock_status_builder,
    ):
        session_id = uuid4()
        response = await get_authorization_status(session_id=session_id, jobId=None, db=MagicMock())

    assert response.jobId == job_id
    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_repo.get_session_data.assert_awaited_once_with(session_id, "authorizationJobId")
    mock_status_builder.assert_awaited_once_with(job_id)


@pytest.mark.asyncio
async def test_override_authorization_success():
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.update_session = AsyncMock()

    code = GroovyCodePayload(code='connector { authorization { header "Authorization" } }')

    with patch("src.modules.codegen.router.SessionRepository", return_value=mock_repo):
        session_id = uuid4()
        response = await override_authorization(
            session_id=session_id,
            authorization_code=code,
            db=MagicMock(),
        )

    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_repo.update_session.assert_awaited_once_with(session_id, {"authorizationOutput": code.model_dump()})
    assert response["message"].startswith("Authorization code overridden successfully")
    assert response["sessionId"] == session_id
