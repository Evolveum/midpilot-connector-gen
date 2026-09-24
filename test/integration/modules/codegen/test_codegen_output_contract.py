# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Actual HTTP serialization of connector outputs and code-only override requests."""

import threading
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from src.core.db import get_db
from src.modules.codegen.repair import NO_CODE_GENERATED
from src.modules.codegen.router import router
from src.modules.codegen.schema import GroovyCodePayload
from src.modules.codegen.utils.connector_code_validation import ensure_valid_connector_code

OPERATIONS = [
    ("authorization", "authorizationOutput"),
    ("classes/user/native-schema", "userNativeSchemaOutput"),
    ("classes/user/connid", "userConnidOutput"),
    ("classes/user/search/all", "userSearchAllOutput"),
    ("classes/user/search/filter", "userSearchFilterOutput"),
    ("classes/user/search/id", "userSearchIdOutput"),
    ("classes/user/create", "userCreateOutput"),
    ("classes/user/update", "userUpdateOutput"),
    ("classes/user/delete", "userDeleteOutput"),
    ("relations/membership", "membershipCodeOutput"),
]


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/codegen")
    app.dependency_overrides[get_db] = lambda: MagicMock()

    # Reference the original synchronous request validation to compare the full
    # HTTP error contract, including Pydantic's error context and input fields.
    @app.put("/legacy-validation")
    async def legacy_validation(payload: GroovyCodePayload):
        return {"code": payload.code}

    return app


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize(("path", "session_key"), OPERATIONS)
@pytest.mark.parametrize(
    ("code", "code_format"),
    [
        ("{}", "YAML"),
        ("# No changes needed. Use framework defaults.\nobjectClasses: {user: {create: {}}}", "YAML"),
        ('objectClass("user") { create { // No changes needed. Use framework defaults.\n} }', "GROOVY"),
        ('objectClass("user") {}', "GROOVY"),
        ("objectClasses: {user: {search: {custom: {implementation: 'return []'}}}}", "YAML"),
        ("", None),
    ],
)
def test_generation_status_keeps_envelope_and_code_format(client, path, session_key, code, code_format):
    session_id, job_id = uuid4(), uuid4()
    result = {"format": code_format, "code": code}
    errors = [NO_CODE_GENERATED] if not code else []
    with (
        patch(
            "src.database.repositories.session_repository.SessionRepository.session_exists",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "src.database.repositories.session_repository.SessionRepository.get_session_data",
            new=AsyncMock(return_value=job_id),
        ),
        patch(
            "src.api.responses.get_job_status",
            new=AsyncMock(
                return_value={
                    "jobId": job_id,
                    "status": "finished",
                    "result": result,
                    "errors": errors,
                }
            ),
        ),
    ):
        response = client.get(f"/api/v1/codegen/{session_id}/{path}")

    assert response.status_code == 200
    body = response.json()
    assert body["jobId"] == str(job_id)
    assert body["status"] == "finished"
    assert body["result"] == result
    assert body["errors"] == errors
    assert "format" not in body and "code" not in body


@pytest.mark.parametrize(("path", "session_key"), OPERATIONS)
@pytest.mark.parametrize(("code", "code_format"), [("{}", "YAML"), ('objectClass("user") {}', "GROOVY")])
def test_code_only_override_persists_derived_format(client, path, session_key, code, code_format):
    session_id = uuid4()
    with (
        patch(
            "src.database.repositories.session_repository.SessionRepository.session_exists",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "src.database.repositories.session_repository.SessionRepository.update_session", new=AsyncMock()
        ) as update,
    ):
        response = client.put(f"/api/v1/codegen/{session_id}/{path}", json={"code": code})

    assert response.status_code == 200
    assert response.json()["sessionId"] == str(session_id)
    update.assert_awaited_once_with(session_id, {session_key: {"format": code_format, "code": code}})


def test_fix_status_reports_each_script_format(client):
    session_id, job_id = uuid4(), uuid4()
    scripts = [
        {"operationKey": "userCreate", "sessionKey": "userCreateOutput", "format": "YAML", "code": "{}"},
        {
            "operationKey": "userUpdate",
            "sessionKey": "userUpdateOutput",
            "format": "GROOVY",
            "code": 'objectClass("user") {}',
        },
    ]
    with (
        patch(
            "src.database.repositories.session_repository.SessionRepository.session_exists",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "src.database.repositories.session_repository.SessionRepository.get_session_data",
            new=AsyncMock(return_value=job_id),
        ),
        patch(
            "src.api.responses.get_job_status",
            new=AsyncMock(
                return_value={
                    "jobId": job_id,
                    "status": "finished",
                    "result": {"scripts": scripts},
                }
            ),
        ),
    ):
        response = client.get(f"/api/v1/codegen/{session_id}/classes/user/fix")

    assert response.status_code == 200
    assert response.json()["status"] == "finished"
    assert response.json()["result"]["scripts"] == scripts


@pytest.mark.parametrize(("path", "session_key"), OPERATIONS)
@pytest.mark.parametrize(
    "body",
    [
        {"code": ""},
        {"code": "objectClasses: {user: {unknownOption: true}}"},
        {"code": 'objectClass("user") {'},
        {"code": "objectClasses: {}\nobjectClasses: {}"},
        {"code": None},
        {"code": 42},
        {},
        [],
    ],
)
def test_override_validation_preserves_http_errors(client, path, session_key, body):
    expected = client.put("/legacy-validation", json=body)
    with patch(
        "src.database.repositories.session_repository.SessionRepository.update_session", new=AsyncMock()
    ) as update:
        response = client.put(f"/api/v1/codegen/{uuid4()}/{path}", json=body)

    assert response.status_code == expected.status_code == 422
    assert response.json() == expected.json()
    update.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "session_key"), OPERATIONS)
@pytest.mark.parametrize(
    ("code", "normalized", "code_format"),
    [
        ("```yaml\n{}\n```", "{}", "YAML"),
        ('  objectClass("user") {}  ', 'objectClass("user") {}', "GROOVY"),
    ],
)
async def test_override_validates_once_outside_event_loop(app, path, session_key, code, normalized, code_format):
    loop_thread = threading.get_ident()
    session_id = uuid4()

    def validate_in_worker(value):
        assert threading.get_ident() != loop_thread
        return ensure_valid_connector_code(value)

    with (
        patch("src.modules.codegen.schema.ensure_valid_connector_code", side_effect=validate_in_worker) as validate,
        patch(
            "src.database.repositories.session_repository.SessionRepository.session_exists",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "src.database.repositories.session_repository.SessionRepository.update_session", new=AsyncMock()
        ) as update,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            response = await http.put(f"/api/v1/codegen/{session_id}/{path}", json={"code": code})

    assert response.status_code == 200
    validate.assert_called_once_with(code)
    update.assert_awaited_once_with(session_id, {session_key: {"format": code_format, "code": normalized}})
