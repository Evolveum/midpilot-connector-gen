# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""midPoint-facing HTTP contract of the session errors that keep FastAPI's ``detail`` envelope.

These responses predate the ``{"error": {code, message}}`` envelope; the domain errors
replacing the former ``HTTPException`` raises must reproduce them exactly.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.app import create_api
from src.core.db import get_db
from src.session.errors import InvalidDocumentationContentError, UnsupportedDocumentationFormatError

UNSUPPORTED_ZIP_DETAIL = (
    "Unsupported documentation content type 'application/zip' for archive.zip. "
    "Supported uploads include JSON, YAML, OpenAPI, Markdown, AsciiDoc, HTML, XML, CSV, SQL, text, PDF, and DOCX."
)


@pytest.fixture()
def client_and_repo():
    app = create_api()
    app.dependency_overrides[get_db] = lambda: MagicMock()
    repo = MagicMock()
    repo.session_exists = AsyncMock(return_value=True)
    repo.get_session_values = AsyncMock(return_value={})
    repo.create_session = AsyncMock(side_effect=RuntimeError("database unavailable"))
    repo.create_session_with_id = AsyncMock(side_effect=RuntimeError("database unavailable"))
    with (
        patch("src.session.routes.sessions.SessionRepository", return_value=repo),
        patch("src.session.routes.documentation.SessionRepository", return_value=repo),
    ):
        yield TestClient(app, raise_server_exceptions=False), repo


def test_empty_upload_keeps_422_detail_contract(client_and_repo):
    client, _ = client_and_repo
    response = client.post(
        f"/api/v1/session/{uuid4()}/documentation",
        files={"documentation": ("empty.md", b"", "text/markdown")},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Uploaded documentation empty.md is empty."}


def test_upload_rejections_registered_by_the_composition_root_keep_detail_envelope(client_and_repo):
    """Parsing runs in the job today, but any request-path rejection must keep the same body."""
    client, _ = client_and_repo

    @client.app.get("/probe/unsupported")
    async def _unsupported():
        raise UnsupportedDocumentationFormatError("application/zip", "archive.zip")

    @client.app.get("/probe/unreadable")
    async def _unreadable():
        raise InvalidDocumentationContentError("Could not extract text from uploaded PDF broken.pdf.")

    unsupported = client.get("/probe/unsupported")
    unreadable = client.get("/probe/unreadable")

    assert unsupported.status_code == 415
    assert unsupported.json() == {"detail": UNSUPPORTED_ZIP_DETAIL}
    assert unreadable.status_code == 422
    assert unreadable.json() == {"detail": "Could not extract text from uploaded PDF broken.pdf."}


@pytest.mark.parametrize("path_suffix", ["", f"/{uuid4()}"])
def test_session_creation_failure_keeps_500_detail_contract(client_and_repo, path_suffix):
    client, repo = client_and_repo
    if path_suffix:
        repo.session_exists = AsyncMock(return_value=False)

    response = client.post(f"/api/v1/session{path_suffix}")

    assert response.status_code == 500
    assert response.json() == {"detail": "Unable to create session"}


def test_other_domain_errors_keep_the_structured_error_envelope(client_and_repo):
    client, repo = client_and_repo
    repo.session_exists = AsyncMock(return_value=True)
    session_id = uuid4()

    response = client.post(f"/api/v1/session/{session_id}")

    assert response.status_code == 409
    assert response.json() == {
        "error": {"code": "session_already_exists", "message": f"Session {session_id} already exists"}
    }
