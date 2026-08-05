# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for request-scoped log correlation."""

from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.api.correlation import bind_session_correlation
from src.core.observability.correlation import current_correlation


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI(dependencies=[Depends(bind_session_correlation)])

    @app.get("/session/{session_id}/thing")
    async def _session_route(session_id: str) -> dict:
        correlation = current_correlation()
        return {"session": str(correlation.session_id), "job": str(correlation.job_id)}

    @app.get("/sessionless")
    async def _sessionless_route() -> dict:
        return {"session": str(current_correlation().session_id)}

    return TestClient(app)


def test_session_route_binds_the_session(client: TestClient):
    session_id = uuid4()

    body = client.get(f"/session/{session_id}/thing").json()

    assert body["session"] == str(session_id)
    assert body["job"] == "None"


def test_route_without_a_session_stays_unattributed(client: TestClient):
    assert client.get("/sessionless").json()["session"] == "None"


def test_malformed_session_id_is_not_guessed(client: TestClient):
    assert client.get("/session/not-a-uuid/thing").json()["session"] == "None"


def test_binding_does_not_leak_between_requests(client: TestClient):
    first = uuid4()
    second = uuid4()

    assert client.get(f"/session/{first}/thing").json()["session"] == str(first)
    assert client.get(f"/session/{second}/thing").json()["session"] == str(second)
    assert client.get("/sessionless").json()["session"] == "None"
    assert current_correlation().session_id is None
