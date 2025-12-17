# Copyright (c) 2025 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import pytest
from fastapi.testclient import TestClient

from src.app import api


@pytest.fixture(scope="module")
def client() -> TestClient:
    """
    Pytest fixture that provides a TestClient for the FastAPI application.

    :return: TestClient instance bound to the FastAPI app.
    """
    return TestClient(api)


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"message": "OK"}
