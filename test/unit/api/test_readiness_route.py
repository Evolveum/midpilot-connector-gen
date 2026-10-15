# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""HTTP contract of GET /api/v1/ready as consumed by midPoint."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.api import readiness as readiness_route
from src.app import create_api
from src.auth.dependencies import API_KEY_HEADER_NAME
from src.config import config
from src.config.auth import AuthMode
from src.core.readiness import ReadinessReport, ReadinessStatus

READY_PATH = "/api/v1/ready"
HEADERS = {API_KEY_HEADER_NAME: "gravitee-key"}


def _report(status: ReadinessStatus) -> ReadinessReport:
    return ReadinessReport(status=status, version="1.2.3")


@pytest.fixture()
def app(monkeypatch):
    monkeypatch.setattr(config.auth, "mode", AuthMode.prod)
    return create_api()


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app)


def _stub_readiness(monkeypatch, report: ReadinessReport) -> AsyncMock:
    check = AsyncMock(return_value=report)
    monkeypatch.setattr(readiness_route, "check_readiness", check)
    return check


def test_ready_requires_api_key(client, monkeypatch):
    check = _stub_readiness(monkeypatch, _report(ReadinessStatus.ok))

    response = client.get(READY_PATH)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_api_key"
    check.assert_not_awaited()


def test_ready_returns_200_when_all_dependencies_are_ok(client, monkeypatch):
    _stub_readiness(monkeypatch, _report(ReadinessStatus.ok))

    response = client.get(READY_PATH, headers=HEADERS)

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "1.2.3"}


def test_ready_returns_503_without_dependency_detail(client, monkeypatch):
    _stub_readiness(monkeypatch, _report(ReadinessStatus.unavailable))

    response = client.get(READY_PATH, headers=HEADERS)

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "version": "1.2.3"}


def test_ready_is_not_exposed_outside_api_prefix(client, monkeypatch):
    _stub_readiness(monkeypatch, _report(ReadinessStatus.ok))

    assert client.get("/ready", headers=HEADERS).status_code == 404


def test_ready_openapi_contract(app):
    schema = app.openapi()
    responses = schema["paths"][READY_PATH]["get"]["responses"]
    components = schema["components"]["schemas"]

    for code in ("200", "503"):
        assert responses[code]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ReadinessReport"
        }
    assert set(components["ReadinessReport"]["properties"]) == {"status", "version"}
    assert set(components["ReadinessReport"]["required"]) == {"status", "version"}
    assert components["ReadinessStatus"]["enum"] == ["ok", "unavailable"]
