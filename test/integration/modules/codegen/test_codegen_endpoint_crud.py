# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Integration tests for codegen create/update/delete endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.modules.codegen.router import generate_create, generate_delete, generate_update
from src.modules.codegen.schema import PreferredEndpointInput


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("generator_fn", "job_type", "session_input_key", "preferred_endpoint"),
    [
        (
            generate_create,
            "codegen.getCreate",
            "UserCreateInput",
            {"method": "POST", "path": "/users"},
        ),
        (
            generate_update,
            "codegen.getUpdate",
            "UserUpdateInput",
            {"method": "PATCH", "path": "/users/{id}"},
        ),
        (
            generate_delete,
            "codegen.getDelete",
            "UserDeleteInput",
            {"method": "DELETE", "path": "/users/{id}"},
        ),
    ],
)
async def test_generate_crud_includes_preferred_endpoint_in_job_and_session_input(
    generator_fn,
    job_type: str,
    session_input_key: str,
    preferred_endpoint: dict,
):
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

        response = await generator_fn(
            session_id,
            "User",
            db=MagicMock(),
            preferred_endpoint_input=PreferredEndpointInput.model_validate({"preferredEndpoint": preferred_endpoint}),
        )

    assert response.jobId == job_id
    mock_schedule.assert_awaited_once()

    _, schedule_kwargs = mock_schedule.call_args
    assert schedule_kwargs["job_type"] == job_type
    assert schedule_kwargs["input_payload"]["preferredEndpoint"] == preferred_endpoint
    assert schedule_kwargs["worker_kwargs"]["preferred_endpoint"] == preferred_endpoint

    update_args = mock_repo.update_session.call_args[0]
    inputs = update_args[1]
    assert inputs[session_input_key]["preferredEndpoint"] == preferred_endpoint
