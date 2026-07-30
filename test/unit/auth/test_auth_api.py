# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""API key authentication and session ownership behavior, exercised through the app.

DB access is stubbed: ``get_db`` is overridden and the repositories used by the
auth dependency and routers are patched, so these tests cover the HTTP contract
(status codes, error codes, masking) rather than persistence.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from src.app import api
from src.auth.keys import generate_api_key, hash_api_key
from src.auth.router import revoke_api_key
from src.config import config
from src.core.db import get_db
from src.database.repositories.session_repository import SessionOwner

MASTER_KEY = "unit-test-master-key"


@pytest.fixture()
def client():
    db = MagicMock()
    db.commit = AsyncMock()
    api.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(api)
    finally:
        api.dependency_overrides.pop(get_db, None)


def _override_auth(monkeypatch, *, api_key_required: bool, master_api_key: SecretStr | None) -> None:
    monkeypatch.setattr(config.auth, "api_key_required", api_key_required)
    monkeypatch.setattr(config.auth, "master_api_key", master_api_key)


@pytest.fixture()
def disabled_auth(monkeypatch):
    """Auth fully off. Pinned explicitly so a configured .env cannot change the mode."""
    _override_auth(monkeypatch, api_key_required=False, master_api_key=None)


@pytest.fixture()
def enforced_auth(monkeypatch):
    _override_auth(monkeypatch, api_key_required=True, master_api_key=SecretStr(MASTER_KEY))


@pytest.fixture()
def master_key_only(monkeypatch):
    """Auth disabled but master key configured (pre-provisioning scenario)."""
    _override_auth(monkeypatch, api_key_required=False, master_api_key=SecretStr(MASTER_KEY))


def _api_key_record(api_key_id=None, name="test key", revoked_at=None):
    generated = generate_api_key()
    return SimpleNamespace(
        api_key_id=api_key_id or uuid4(),
        name=name,
        key_prefix=generated.prefix,
        key_hash=generated.hash,
        created_at=datetime.now(timezone.utc),
        revoked_at=revoked_at,
    )


def _patch_auth_repos(active_record=None, session_owner=None):
    """Patch the repositories used by the auth dependency."""
    api_key_repo = MagicMock()
    api_key_repo.get_active_key_by_hash = AsyncMock(return_value=active_record)
    session_repo = MagicMock()
    session_repo.get_session_owner = AsyncMock(return_value=session_owner)
    return (
        patch("src.auth.dependencies.ApiKeyRepository", return_value=api_key_repo),
        patch("src.session.ownership.SessionRepository", return_value=session_repo),
    )


# --- Disabled mode (default) ---


def test_disabled_mode_allows_requests_without_key(client, disabled_auth):
    session_id = uuid4()
    with patch("src.session.routes.sessions.ensure_session_exists", AsyncMock()):
        response = client.head(f"/api/v1/session/{session_id}")

    assert response.status_code == 204


def test_management_unavailable_without_configured_master_key(client, disabled_auth):
    response = client.get("/api/v1/apiKeys")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "master_key_required"


def test_management_works_in_disabled_mode_with_master_key(client, master_key_only):
    api_key_repo = MagicMock()
    api_key_repo.list_api_keys = AsyncMock(return_value=[])
    with patch("src.auth.router.ApiKeyRepository", return_value=api_key_repo):
        response = client.get("/api/v1/apiKeys", headers={"X-API-Key": MASTER_KEY})

    assert response.status_code == 200
    assert response.json() == {"apiKeys": [], "total": 0}


# --- Enforced mode: authentication ---


def test_missing_key_is_rejected(client, enforced_auth):
    response = client.get(f"/api/v1/session/{uuid4()}")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_api_key"


def test_unknown_key_is_rejected(client, enforced_auth):
    repo_patches = _patch_auth_repos(active_record=None)
    with repo_patches[0], repo_patches[1]:
        response = client.get(f"/api/v1/session/{uuid4()}", headers={"X-API-Key": "mpcg_unknown"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


def test_master_key_is_accepted(client, enforced_auth):
    with patch("src.session.routes.sessions.ensure_session_exists", AsyncMock()):
        response = client.head(f"/api/v1/session/{uuid4()}", headers={"X-API-Key": MASTER_KEY})

    assert response.status_code == 204


# --- Enforced mode: session ownership ---


def _get_session_with_key(client, session_owner):
    """GET an existing session using a valid non-master key; returns the response."""
    key_id = uuid4()
    record = _api_key_record(api_key_id=key_id)
    owner = SessionOwner(session_id=uuid4(), api_key_id=session_owner(key_id))
    repo_patches = _patch_auth_repos(active_record=record, session_owner=owner)
    session_repo = MagicMock()
    session_repo.get_session = AsyncMock(
        return_value={
            "sessionId": str(owner.session_id),
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
            "data": {},
        }
    )
    with (
        repo_patches[0],
        repo_patches[1],
        patch("src.session.routes.sessions.SessionRepository", return_value=session_repo),
    ):
        return client.get(f"/api/v1/session/{owner.session_id}", headers={"X-API-Key": "mpcg_valid"})


def test_own_session_is_accessible(client, enforced_auth):
    response = _get_session_with_key(client, session_owner=lambda key_id: key_id)

    assert response.status_code == 200


def test_foreign_session_is_masked_as_not_found(client, enforced_auth):
    response = _get_session_with_key(client, session_owner=lambda key_id: uuid4())

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


def test_ownerless_session_is_master_only(client, enforced_auth):
    response = _get_session_with_key(client, session_owner=lambda key_id: None)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


def test_nonexistent_session_passes_ownership_gate(client, enforced_auth):
    """The gate must not decide for nonexistent sessions - handlers own that 404."""
    record = _api_key_record()
    repo_patches = _patch_auth_repos(active_record=record, session_owner=None)
    with (
        repo_patches[0],
        repo_patches[1],
        patch("src.session.routes.sessions.ensure_session_exists", AsyncMock()),
    ):
        response = client.head(f"/api/v1/session/{uuid4()}", headers={"X-API-Key": "mpcg_valid"})

    assert response.status_code == 204


def test_created_session_records_owning_key(client, enforced_auth):
    key_id = uuid4()
    record = _api_key_record(api_key_id=key_id)
    repo_patches = _patch_auth_repos(active_record=record)
    session_repo = MagicMock()
    session_repo.create_session = AsyncMock(return_value=uuid4())
    with (
        repo_patches[0],
        repo_patches[1],
        patch("src.session.routes.sessions.SessionRepository", return_value=session_repo),
    ):
        response = client.post("/api/v1/session", headers={"X-API-Key": "mpcg_valid"})

    assert response.status_code == 201
    session_repo.create_session.assert_awaited_once_with(api_key_id=key_id)


# --- Management endpoints ---


def test_create_api_key_returns_full_value_once(client, enforced_auth):
    api_key_repo = MagicMock()

    async def _create(name, key_prefix, key_hash):
        record = _api_key_record(name=name)
        record.key_prefix = key_prefix
        record.key_hash = key_hash
        return record

    api_key_repo.create_api_key = AsyncMock(side_effect=_create)
    with patch("src.auth.router.ApiKeyRepository", return_value=api_key_repo):
        response = client.post("/api/v1/apiKeys", json={"name": "midPilot prod"}, headers={"X-API-Key": MASTER_KEY})

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "midPilot prod"
    assert body["apiKey"].startswith("mpcg_")
    assert body["keyPrefix"] == body["apiKey"][:10]
    # the stored hash must match the returned value
    stored = api_key_repo.create_api_key.await_args.kwargs
    assert stored["key_hash"] == hash_api_key(body["apiKey"])


def test_list_api_keys_never_exposes_values(client, enforced_auth):
    record = _api_key_record()
    api_key_repo = MagicMock()
    api_key_repo.list_api_keys = AsyncMock(return_value=[record])
    with patch("src.auth.router.ApiKeyRepository", return_value=api_key_repo):
        response = client.get("/api/v1/apiKeys", headers={"X-API-Key": MASTER_KEY})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert set(body["apiKeys"][0]) == {"apiKeyId", "name", "keyPrefix", "createdAt", "revokedAt"}


def test_management_rejects_non_master_key(client, enforced_auth):
    record = _api_key_record()
    repo_patches = _patch_auth_repos(active_record=record)
    with repo_patches[0], repo_patches[1]:
        response = client.get("/api/v1/apiKeys", headers={"X-API-Key": "mpcg_valid"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "master_key_required"


def test_revoke_unknown_api_key_returns_not_found(client, enforced_auth):
    api_key_repo = MagicMock()
    api_key_repo.revoke_api_key = AsyncMock(return_value=None)
    with patch("src.auth.router.ApiKeyRepository", return_value=api_key_repo):
        response = client.delete(f"/api/v1/apiKeys/{uuid4()}", headers={"X-API-Key": MASTER_KEY})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "api_key_not_found"


def test_revoke_api_key_returns_revocation_time(client, enforced_auth):
    record = _api_key_record(revoked_at=datetime.now(timezone.utc))
    api_key_repo = MagicMock()
    api_key_repo.revoke_api_key = AsyncMock(return_value=record)
    with patch("src.auth.router.ApiKeyRepository", return_value=api_key_repo):
        response = client.delete(f"/api/v1/apiKeys/{record.api_key_id}", headers={"X-API-Key": MASTER_KEY})

    assert response.status_code == 200
    body = response.json()
    assert body["apiKeyId"] == str(record.api_key_id)
    assert body["revokedAt"] is not None


@pytest.mark.asyncio
async def test_revoke_api_key_commits_before_returning_success() -> None:
    record = _api_key_record(revoked_at=datetime.now(timezone.utc))
    api_key_repo = MagicMock()
    api_key_repo.revoke_api_key = AsyncMock(return_value=record)
    db = MagicMock()
    db.commit = AsyncMock()

    with patch("src.auth.router.ApiKeyRepository", return_value=api_key_repo):
        response = await revoke_api_key(record.api_key_id, db)

    db.commit.assert_awaited_once_with()
    assert response.api_key_id == record.api_key_id
