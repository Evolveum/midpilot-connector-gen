# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for the centralized exception handlers."""

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.common.errors import (
    InvalidObjectClassesOutputError,
    ObjectClassesNotFoundError,
    RelevantChunksNotFoundError,
)
from src.common.exception_handlers import register_exception_handlers


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/app-404")
    async def _app_404():
        raise ObjectClassesNotFoundError()

    @app.get("/app-400")
    async def _app_400():
        raise RelevantChunksNotFoundError("User", "attributes")

    @app.get("/app-422")
    async def _app_422():
        raise InvalidObjectClassesOutputError(uuid4())

    @app.get("/unexpected")
    async def _unexpected():
        raise RuntimeError("internal detail that must not leak")

    return TestClient(app, raise_server_exceptions=False)


def test_app_error_maps_to_status_and_structured_body(client: TestClient) -> None:
    response = client.get("/app-404")
    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "object_classes_not_found",
            "message": "No object classes found in session. Please run /classes endpoint first.",
        }
    }


def test_app_error_status_codes_follow_the_exception(client: TestClient) -> None:
    assert client.get("/app-400").json()["error"]["code"] == "relevant_chunks_not_found"
    assert client.get("/app-400").status_code == 400
    assert client.get("/app-422").json()["error"]["code"] == "invalid_object_classes_output"
    assert client.get("/app-422").status_code == 422


def test_unexpected_error_returns_generic_500_without_leaking_internals(client: TestClient) -> None:
    response = client.get("/unexpected")
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error", "message": "Internal server error"}}
    assert "internal detail that must not leak" not in response.text
