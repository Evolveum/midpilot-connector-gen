# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Backend contracts for gateway identity and session isolation (no live gateway)."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.app import create_api
from src.auth.dependencies import API_KEY_HEADER_NAME, authenticate_request
from src.auth.keys import hash_api_key
from src.config import config
from src.config.auth import AuthMode, AuthSettings
from src.core.db import get_db
from src.database.repositories.session_repository import SessionOwner

KEY_A = "gravitee-key-a"
KEY_B = "gravitee-key-b"
HEADERS_A = {API_KEY_HEADER_NAME: KEY_A}
HEADERS_B = {API_KEY_HEADER_NAME: KEY_B}


@pytest.fixture()
def gateway_api(monkeypatch):
    monkeypatch.setattr(config.auth, "mode", AuthMode.prod)
    app = create_api()
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    session_id = uuid4()
    repo = MagicMock()
    repo.get_session_owner = AsyncMock(return_value=SessionOwner(session_id, hash_api_key(KEY_A)))
    repo.create_session = AsyncMock(return_value=session_id)
    repo.create_session_with_id = AsyncMock(return_value=session_id)
    repo.session_exists = AsyncMock(return_value=False)
    repo.get_session = AsyncMock(
        return_value={
            "sessionId": str(session_id),
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
            "data": {},
        }
    )
    with (
        patch("src.session.ownership.SessionRepository", return_value=repo),
        patch("src.session.routes.sessions.SessionRepository", return_value=repo),
    ):
        yield TestClient(app), repo, session_id


def test_default_requires_gateway_and_unknown_mode_fails():
    assert AuthSettings().mode is AuthMode.prod
    assert AuthSettings.model_validate({"mode": "dev"}).mode is AuthMode.dev
    assert AuthSettings.model_validate({"mode": "prod"}).mode is AuthMode.prod
    for mode in ("unknown", "gravitee", "development"):
        with pytest.raises(ValidationError):
            AuthSettings.model_validate({"mode": mode})


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": KEY_A}])
def test_missing_gateway_header_is_rejected(gateway_api, headers):
    client, repo, _ = gateway_api
    response = client.post("/api/v1/session", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_api_key"
    repo.create_session.assert_not_awaited()


@pytest.mark.parametrize("value", ["", " ", "key other", "key,other", " key", "key\tother"])
def test_malformed_gateway_header_is_rejected_without_echoing_secret(gateway_api, value):
    client, repo, _ = gateway_api
    response = client.post("/api/v1/session", headers={API_KEY_HEADER_NAME: value})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"
    repo.create_session.assert_not_awaited()


@pytest.mark.parametrize("second", [KEY_A, KEY_B])
def test_duplicate_gateway_headers_are_rejected(gateway_api, second):
    client, repo, _ = gateway_api
    response = client.post(
        "/api/v1/session", headers=[(API_KEY_HEADER_NAME, KEY_A), (API_KEY_HEADER_NAME.lower(), second)]
    )
    assert response.status_code == 401
    repo.create_session.assert_not_awaited()


def test_query_parameter_does_not_supply_identity(gateway_api):
    client, _, _ = gateway_api
    assert client.post("/api/v1/session", params={"api-key": KEY_A}).status_code == 401


@pytest.mark.asyncio
async def test_identity_resolution_needs_no_database_and_retains_no_raw_key(monkeypatch):
    monkeypatch.setattr(config.auth, "mode", AuthMode.prod)
    request = Request({"type": "http", "headers": [(b"x-gravitee-api-key", KEY_A.encode())]})
    context = await authenticate_request(request, KEY_A)
    assert context.owner_key_hash == hash_api_key(KEY_A)
    assert request.state.auth is context
    assert KEY_A not in repr(context)


def test_create_session_persists_fingerprint_and_preserves_response(gateway_api):
    client, repo, session_id = gateway_api
    response = client.post("/api/v1/session", headers=HEADERS_A)
    assert response.status_code == 201
    assert response.json() == {
        "sessionId": str(session_id),
        "message": "Session created successfully. Use this session_id in subsequent requests.",
    }
    repo.create_session.assert_awaited_once_with(owner_key_hash=hash_api_key(KEY_A))
    assert KEY_A not in response.text


def test_create_with_id_persists_fingerprint(gateway_api):
    client, repo, session_id = gateway_api
    repo.get_session_owner.return_value = None
    response = client.post(f"/api/v1/session/{session_id}", headers=HEADERS_A)
    assert response.status_code == 201
    repo.create_session_with_id.assert_awaited_once_with(session_id, owner_key_hash=hash_api_key(KEY_A))


def test_owner_can_read_session_and_legacy_header_cannot_change_owner(gateway_api):
    client, repo, session_id = gateway_api
    response = client.get(f"/api/v1/session/{session_id}", headers={**HEADERS_A, "X-API-Key": KEY_B})
    assert response.status_code == 200
    assert response.json() == {
        "sessionId": str(session_id),
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
        "message": "Session returned successfully.",
    }
    repo.get_session.assert_awaited_once_with(session_id)


@pytest.mark.parametrize("owner", [hash_api_key(KEY_B), None])
def test_foreign_or_development_session_is_hidden(gateway_api, owner, caplog):
    client, repo, session_id = gateway_api
    repo.get_session_owner.return_value = SessionOwner(session_id, owner)
    response = client.get(f"/api/v1/session/{session_id}", headers=HEADERS_A)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"
    repo.get_session.assert_not_awaited()
    assert KEY_A not in caplog.text
    assert hash_api_key(KEY_A) not in caplog.text


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/session/{session_id}"),
        ("GET", "/session/{session_id}/jobs"),
        ("HEAD", "/session/{session_id}"),
        ("GET", "/digester/{session_id}/classes/GROUP/documentation"),
    ],
)
def test_foreign_key_is_blocked_across_session_routes(gateway_api, method, path):
    client, repo, session_id = gateway_api
    response = client.request(method, "/api/v1" + path.format(session_id=session_id), headers=HEADERS_B)
    assert response.status_code == 404
    repo.create_session_with_id.assert_not_awaited()


def test_nonexistent_session_reaches_handler(gateway_api):
    client, repo, session_id = gateway_api
    repo.get_session_owner.return_value = None
    repo.get_session.return_value = None
    response = client.get(f"/api/v1/session/{session_id}", headers=HEADERS_A)
    assert response.status_code == 404
    repo.get_session.assert_awaited_once_with(session_id)


def test_development_is_explicit_and_creates_ownerless_sessions(gateway_api, monkeypatch):
    client, repo, session_id = gateway_api
    monkeypatch.setattr(config.auth, "mode", AuthMode.dev)
    assert client.post("/api/v1/session").status_code == 201
    assert client.post("/api/v1/session", headers=HEADERS_A).status_code == 201
    assert all(call.kwargs == {"owner_key_hash": None} for call in repo.create_session.await_args_list)
    assert client.get(f"/api/v1/session/{session_id}").status_code == 200
    repo.get_session_owner.assert_not_awaited()


def test_key_management_routes_and_schemas_are_removed(gateway_api):
    client, _, _ = gateway_api
    for method, path in [("POST", "/apiKeys"), ("GET", "/apiKeys"), ("DELETE", f"/apiKeys/{uuid4()}")]:
        assert client.request(method, "/api/v1" + path, headers=HEADERS_A).status_code == 404
    schema = client.app.openapi()
    assert not any("apiKeys" in path for path in schema["paths"])
    assert not any(name.startswith("ApiKey") for name in schema["components"]["schemas"])
    assert schema["components"]["securitySchemes"]["APIKeyHeader"]["name"] == API_KEY_HEADER_NAME


def test_health_remains_public(gateway_api):
    client, _, _ = gateway_api
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"message": "OK"}
