#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

import pytest
from fastapi.testclient import TestClient

from src.app import api


@pytest.fixture(scope="module")
def client():
    return TestClient(api)


def test_camel_case_url_convention(client):
    response = client.get("/openapi.json")
    schema = response.json()

    assert response.status_code == 200

    for path in schema["paths"]:
        for subpath in path.split("/"):
            if subpath and not subpath.startswith("{"):
                assert subpath[0].islower()


def test_camel_case_parameter_convention(client):
    response = client.get("/openapi.json")
    schema = response.json()

    assert response.status_code == 200

    types = schema["components"]["schemas"]
    for schema_name in types:
        if "properties" not in types[schema_name]:
            continue
        for property_name in types[schema_name]["properties"]:
            assert property_name[0].islower()
