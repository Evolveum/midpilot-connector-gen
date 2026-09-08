# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""HTTP contracts for source retrieval, using the real router and ownership guard."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.app import create_api
from src.auth.context import AuthContext, AuthMode
from src.auth.dependencies import authenticate_request
from src.config import config
from src.config.digester import DigesterSettings
from src.core.db import get_db


@pytest.fixture()
def documentation_api():
    owner_id = uuid4()
    session_id = uuid4()
    repo = MagicMock()
    repo.session_exists = AsyncMock(return_value=True)
    repo.get_session_owner = AsyncMock(return_value=SimpleNamespace(api_key_id=owner_id))
    outputs = {
        "objectClassesOutput": {"objectClasses": [{"name": "GROUP"}]},
        "groupEndpointsOutput": {
            "endpoints": [
                {"method": "GET", "path": "/groups"},
                {"method": "POST", "path": "/groups"},
                {"method": "DELETE", "path": "/groups/{id}"},
            ]
        },
    }
    repo.get_session_data = AsyncMock(side_effect=lambda _session, key: outputs.get(key))
    doc_repo = MagicMock()
    doc_repo.get_relevant_documentation_items = AsyncMock(return_value=[])

    async def authenticate(request: Request):
        request.state.auth = AuthContext(AuthMode.api_key, owner_id)

    app = create_api()
    app.dependency_overrides[authenticate_request] = authenticate
    app.dependency_overrides[get_db] = lambda: MagicMock()
    with (
        patch("src.modules.digester.routes.documentation.SessionRepository", return_value=repo),
        patch("src.session.ownership.SessionRepository", return_value=repo),
        patch("src.modules.digester.routes.object_classes.SessionRepository", return_value=repo),
        patch("src.modules.digester.documentation.DocumentationRepository", return_value=doc_repo),
    ):
        yield SimpleNamespace(
            client=TestClient(app, raise_server_exceptions=False),
            repo=repo,
            doc_repo=doc_repo,
            outputs=outputs,
            session_id=session_id,
            url=f"{config.app.api_base_url}/v1/digester/{session_id}/classes/GROUP/documentation",
        )


def test_class_content_contract_and_next_page(documentation_api):
    api = documentation_api
    doc_id, chunk_id = uuid4(), uuid4()
    row = {
        "docId": str(doc_id),
        "chunkId": str(chunk_id),
        "source": "upload",
        "url": None,
        "content": "# Groups\n\n<script>untrusted</script>\n",
        "metadata": {
            "filename": "groups.md",
            "content_type": "text/markdown",
            "chunk_number": 2,
        },
    }
    api.doc_repo.get_relevant_documentation_items.return_value = [row, row]
    response = api.client.get(api.url.replace("GROUP", "gRoUp"), params={"limit": 1, "offset": 2})
    assert response.status_code == 200
    assert response.json() == {
        "objectClass": "GROUP",
        "endpoint": None,
        "nextOffset": 3,
        "items": [
            {
                "docId": str(doc_id),
                "chunkId": str(chunk_id),
                "source": "upload",
                "url": None,
                "content": row["content"],
                "filename": "groups.md",
                "contentType": "text/markdown",
                "chunkNumber": 2,
            }
        ],
    }
    api.doc_repo.get_relevant_documentation_items.assert_awaited_once_with(
        api.session_id,
        result_key="objectClassesOutput",
        entity_key="group",
        offset=2,
        limit=2,
    )


@pytest.mark.parametrize("method,path", [("get", "/groups"), (" POST ", "/groups"), ("DELETE", "/groups/{id}")])
def test_endpoint_selection_and_no_reference_fallback(documentation_api, method, path):
    api = documentation_api
    response = api.client.get(api.url, params={"method": method, "path": path})
    assert response.status_code == 200
    assert response.json() == {
        "objectClass": "GROUP",
        "endpoint": {"method": method.strip().upper(), "path": path},
        "items": [],
        "nextOffset": None,
    }
    api.doc_repo.get_relevant_documentation_items.assert_awaited_once_with(
        api.session_id,
        result_key="groupEndpointsOutput",
        entity_key=f"{method.strip().upper()} {path}",
        offset=0,
        limit=config.digester.documentation_page_size + 1,
    )


@pytest.mark.parametrize(
    "params",
    [
        {"method": "POST"},
        {"path": "/groups"},
        {"method": "POST", "path": " "},
        {"method": "INVALID", "path": "/groups"},
        {"offset": -1},
        {"limit": 0},
        {"limit": config.digester.documentation_max_page_size + 1},
    ],
)
def test_invalid_query_returns_422(documentation_api, params):
    assert documentation_api.client.get(documentation_api.url, params=params).status_code == 422
    documentation_api.doc_repo.get_relevant_documentation_items.assert_not_awaited()


@pytest.mark.parametrize("method,path", [("POST", "/Groups"), ("PUT", "/groups"), ("GET", "/missing")])
def test_unknown_endpoint_returns_404(documentation_api, method, path):
    api = documentation_api
    response = api.client.get(api.url, params={"method": method, "path": path})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "documentation_endpoint_not_found"
    api.doc_repo.get_relevant_documentation_items.assert_not_awaited()


@pytest.mark.parametrize("missing", ["session", "class", "classes", "endpoints"])
def test_missing_selection_returns_404(documentation_api, missing):
    api = documentation_api
    params = {}
    if missing == "session":
        api.repo.session_exists.return_value = False
    elif missing == "class":
        api.outputs["objectClassesOutput"]["objectClasses"] = []
    elif missing == "classes":
        del api.outputs["objectClassesOutput"]
    else:
        del api.outputs["groupEndpointsOutput"]
        params = {"method": "POST", "path": "/groups"}
    assert api.client.get(api.url, params=params).status_code == 404
    api.doc_repo.get_relevant_documentation_items.assert_not_awaited()


def test_other_owners_session_is_hidden(documentation_api):
    api = documentation_api
    api.repo.get_session_owner.return_value = SimpleNamespace(api_key_id=uuid4())
    assert api.client.get(api.url).status_code == 404
    api.repo.get_session_data.assert_not_awaited()
    api.doc_repo.get_relevant_documentation_items.assert_not_awaited()


def test_database_error_is_not_an_empty_success(documentation_api):
    api = documentation_api
    api.doc_repo.get_relevant_documentation_items.side_effect = RuntimeError("database unavailable")
    assert api.client.get(api.url).status_code == 500


@pytest.mark.parametrize("key", ["objectClassesOutput", "groupEndpointsOutput"])
def test_malformed_stored_result_returns_422(documentation_api, key):
    api = documentation_api
    api.outputs[key] = {"objectClasses": "invalid"} if key == "objectClassesOutput" else {"unexpected": []}
    response = api.client.get(api.url, params={"method": "POST", "path": "/groups"})
    assert response.status_code == 422
    api.doc_repo.get_relevant_documentation_items.assert_not_awaited()


def test_scraped_content_has_nullable_metadata_and_last_page(documentation_api):
    api = documentation_api
    api.doc_repo.get_relevant_documentation_items.return_value = [
        {
            "docId": str(uuid4()),
            "chunkId": str(uuid4()),
            "content": "Groups API",
            "source": "scraper",
            "url": "https://example.test/groups",
            "metadata": {},
        }
    ]
    response = api.client.get(api.url)
    assert response.status_code == 200
    body = response.json()
    assert body["nextOffset"] is None
    assert body["items"][0]["url"] == "https://example.test/groups"
    assert body["items"][0]["filename"] is None
    assert body["items"][0]["contentType"] is None
    assert body["items"][0]["chunkNumber"] is None


def test_documentation_page_settings_validate_bounds():
    assert DigesterSettings(documentation_page_size=2, documentation_max_page_size=3).documentation_page_size == 2
    for settings in [
        {"documentation_page_size": 0},
        {"documentation_max_page_size": 0},
        {"documentation_page_size": 51, "documentation_max_page_size": 50},
    ]:
        with pytest.raises(ValidationError):
            DigesterSettings(**settings)


@pytest.mark.parametrize(
    "output,status,code",
    [
        (None, 404, "object_classes_not_found"),
        ({}, 404, "object_classes_not_found"),
        ("invalid", 404, "object_classes_not_found"),
        ({"foo": 1}, 404, "object_class_not_found"),
        ({"objectClasses": []}, 404, "object_class_not_found"),
        ({"objectClasses": None}, 422, "invalid_object_classes_output"),
        ({"objectClasses": {}}, 422, "invalid_object_classes_output"),
    ],
)
def test_documentation_and_class_detail_share_lookup_errors(documentation_api, output, status, code):
    api = documentation_api
    api.outputs["objectClassesOutput"] = output
    for url in [api.url, api.url.removesuffix("/documentation")]:
        response = api.client.get(url)
        assert response.status_code == status
        assert response.json()["error"]["code"] == code
    api.doc_repo.get_relevant_documentation_items.assert_not_awaited()


def test_empty_panel_logs_selection_without_content_or_correlation_ids(documentation_api, caplog):
    api = documentation_api
    with caplog.at_level(logging.DEBUG, logger="src.modules.digester.documentation"):
        response = api.client.get(api.url, params={"method": "POST", "path": "/groups", "offset": 3})
    assert response.status_code == 200
    records = [record for record in caplog.records if record.name == "src.modules.digester.documentation"]
    assert len(records) == 1
    message = records[0].getMessage()
    assert message == (
        "[Digester:Documentation] Returned 0 chunks for class GROUP result groupEndpointsOutput "
        "entity POST /groups offset 3 has_more=False"
    )
    assert str(api.session_id) not in message
