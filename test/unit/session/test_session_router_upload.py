# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.core.db import get_db
from src.session.routes.documentation import router
from src.session.schema import PreparedDocumentationUpload, RawUploadedDocumentation, SessionUploadContext


@pytest.mark.asyncio
async def test_reupload_preserves_queued_response_contract_and_does_not_predelete():
    session_id, doc_id, job_id = (uuid4() for _ in range(3))
    app = FastAPI()
    app.include_router(router, prefix="/session")
    db = MagicMock()

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    prepared = PreparedDocumentationUpload(
        raw_upload=RawUploadedDocumentation(
            data=b"new", filename="docs.md", content_type="text/markdown", content_hash="hash"
        ),
        context=SessionUploadContext(app="Example", app_version="1.0"),
    )
    with (
        patch("src.session.routes.documentation.ensure_session_exists", new_callable=AsyncMock),
        patch(
            "src.session.routes.documentation.prepare_documentation_upload",
            new_callable=AsyncMock,
            return_value=prepared,
        ),
        patch(
            "src.session.documentation_upload.DocumentationRepository.has_document",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("src.session.documentation_upload.schedule_coroutine_job", new_callable=AsyncMock, return_value=job_id),
        patch("src.session.documentation_upload.SessionRepository.update_session", new_callable=AsyncMock),
        patch(
            "src.session.routes.documentation.DocumentationRepository.remove_documentation_items_by_doc_id",
            new_callable=AsyncMock,
        ) as delete_one,
        patch(
            "src.session.routes.documentation.DocumentationRepository.delete_documentation_items_by_session",
            new_callable=AsyncMock,
        ) as delete_all,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/session/{session_id}/documentation/{doc_id}",
                files={"documentation": ("docs.md", b"new", "text/markdown")},
            )
    assert response.status_code == 200
    assert response.json() == {
        "message": "Documentation upload queued for processing",
        "sessionId": str(session_id),
        "jobId": str(job_id),
        "docId": str(doc_id),
        "status": "queued",
    }
    delete_one.assert_not_awaited()
    delete_all.assert_not_awaited()
