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
from src.core.readiness import ComponentStatus, ReadinessChecks, ReadinessReport

READY_PATH = "/api/v1/ready"
HEADERS = {API_KEY_HEADER_NAME: "gravitee-key"}


def _report(database: ComponentStatus, llm: ComponentStatus) -> ReadinessReport:
    all_ok = database is ComponentStatus.ok and llm is ComponentStatus.ok
    overall = ComponentStatus.ok if all_ok else ComponentStatus.unavailable
    return ReadinessReport(
        status=overall,
        version="1.2.3",
        checks=ReadinessChecks(database=database, llm=llm),
    )


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
    check = _stub_readiness(monkeypatch, _report(ComponentStatus.ok, ComponentStatus.ok))

    response = client.get(READY_PATH)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_api_key"
    check.assert_not_awaited()


def test_ready_returns_200_when_all_dependencies_are_ok(client, monkeypatch):
    _stub_readiness(monkeypatch, _report(ComponentStatus.ok, ComponentStatus.ok))

    response = client.get(READY_PATH, headers=HEADERS)

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "1.2.3", "checks": {"database": "ok", "llm": "ok"}}


@pytest.mark.parametrize(
    ("database", "llm"),
    [
        (ComponentStatus.ok, ComponentStatus.unavailable),
        (ComponentStatus.unavailable, ComponentStatus.ok),
    ],
)
def test_ready_returns_503_with_component_detail(client, monkeypatch, database, llm):
    _stub_readiness(monkeypatch, _report(database, llm))

    response = client.get(READY_PATH, headers=HEADERS)

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "version": "1.2.3",
        "checks": {"database": database.value, "llm": llm.value},
    }


def test_ready_is_not_exposed_outside_api_prefix(client, monkeypatch):
    _stub_readiness(monkeypatch, _report(ComponentStatus.ok, ComponentStatus.ok))

    assert client.get("/ready", headers=HEADERS).status_code == 404


def test_ready_openapi_contract(app):
    schema = app.openapi()
    responses = schema["paths"][READY_PATH]["get"]["responses"]
    components = schema["components"]["schemas"]

    for code in ("200", "503"):
        assert responses[code]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ReadinessReport"
        }
    assert set(components["ReadinessReport"]["properties"]) == {"status", "version", "checks"}
    assert set(components["ReadinessReport"]["required"]) == {"status", "version", "checks"}
    assert set(components["ReadinessChecks"]["properties"]) == {"database", "llm"}
    assert components["ComponentStatus"]["enum"] == ["ok", "unavailable"]
