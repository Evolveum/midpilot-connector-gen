# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from src.common.session.router import export_documentation, import_documentation
from src.common.session.schema import DocumentationExportDocument


@pytest.mark.asyncio
async def test_export_documentation_returns_roundtrip_payload() -> None:
    session_id = uuid4()
    doc_id = uuid4()
    chunk_id_1 = uuid4()
    chunk_id_2 = uuid4()

    mock_session_repo = MagicMock()
    mock_session_repo.session_exists = AsyncMock(return_value=True)

    mock_doc_repo = MagicMock()
    mock_doc_repo.get_documentation_items_for_export = AsyncMock(
        return_value=[
            {
                "chunkId": str(chunk_id_1),
                "docId": str(doc_id),
                "source": "upload",
                "url": "upload://connector.json",
                "summary": "summary 1",
                "content": "content 1",
                "metadata": {"chunk_number": 0},
                "createdAt": "2026-04-02T12:00:00+00:00",
                "scrapeJobIds": [],
            },
            {
                "chunkId": str(chunk_id_2),
                "docId": str(doc_id),
                "source": "upload",
                "url": "upload://connector.json",
                "summary": "summary 2",
                "content": "content 2",
                "metadata": {"chunk_number": 1},
                "createdAt": "2026-04-02T12:00:01+00:00",
                "scrapeJobIds": ["job-1"],
            },
        ]
    )

    with (
        patch("src.common.session.router.SessionRepository", return_value=mock_session_repo),
        patch("src.common.session.router.DocumentationRepository", return_value=mock_doc_repo),
    ):
        payload = await export_documentation(session_id=session_id, db=MagicMock())

    assert isinstance(payload, list)
    assert len(payload) == 1
    payload_json = payload[0].model_dump(by_alias=True, mode="json")
    assert payload_json["docId"] == str(doc_id)
    assert len(payload_json["chunks"]) == 2
    assert payload_json["chunks"][0]["chunkId"] == str(chunk_id_1)
    assert payload_json["chunks"][1]["chunkId"] == str(chunk_id_2)


@pytest.mark.asyncio
async def test_import_documentation_replaces_session_docs() -> None:
    session_id = uuid4()
    doc_id = uuid4()
    chunk_id = uuid4()

    mock_session_repo = MagicMock()
    mock_session_repo.session_exists = AsyncMock(return_value=True)
    mock_session_repo.update_session = AsyncMock()

    mock_doc_repo = MagicMock()
    mock_doc_repo.delete_documentation_items_by_session = AsyncMock()
    mock_doc_repo.import_documentation_items_for_session = AsyncMock(return_value=1)

    payload = [
        DocumentationExportDocument.model_validate(
            {
                "docId": str(doc_id),
                "chunks": [
                    {
                        "chunkId": str(chunk_id),
                        "source": "upload",
                        "url": "upload://connector.json",
                        "summary": "summary",
                        "content": "content",
                        "metadata": {"length": 7},
                        "createdAt": "2026-04-02T12:00:00+00:00",
                        "scrapeJobIds": [],
                    }
                ],
            }
        )
    ]

    with (
        patch("src.common.session.router.SessionRepository", return_value=mock_session_repo),
        patch("src.common.session.router.DocumentationRepository", return_value=mock_doc_repo),
    ):
        response = await import_documentation(payload, session_id=session_id, db=MagicMock())

    assert response["importedDocuments"] == 1
    assert response["importedChunks"] == 1

    mock_doc_repo.delete_documentation_items_by_session.assert_awaited_once_with(session_id)
    mock_doc_repo.import_documentation_items_for_session.assert_awaited_once()
    assert mock_session_repo.update_session.await_count == 2
    assert mock_session_repo.update_session.await_args_list[0].args == (session_id, {"documentationItems": []})


@pytest.mark.asyncio
async def test_import_documentation_returns_409_on_db_conflict() -> None:
    session_id = uuid4()
    chunk_id = uuid4()

    mock_session_repo = MagicMock()
    mock_session_repo.session_exists = AsyncMock(return_value=True)
    mock_session_repo.update_session = AsyncMock()

    mock_doc_repo = MagicMock()
    mock_doc_repo.delete_documentation_items_by_session = AsyncMock()
    mock_doc_repo.import_documentation_items_for_session = AsyncMock(
        side_effect=IntegrityError(
            statement="INSERT INTO documentation_items ...",
            params={},
            orig=Exception("duplicate key value violates unique constraint documentation_items_pkey"),
        )
    )

    payload = [
        DocumentationExportDocument.model_validate(
            {
                "docId": None,
                "chunks": [
                    {
                        "chunkId": str(chunk_id),
                        "source": "upload",
                        "url": "upload://connector.json",
                        "summary": "summary",
                        "content": "content",
                        "metadata": {},
                        "createdAt": "2026-04-02T12:00:00+00:00",
                        "scrapeJobIds": [],
                    }
                ],
            }
        )
    ]

    with (
        patch("src.common.session.router.SessionRepository", return_value=mock_session_repo),
        patch("src.common.session.router.DocumentationRepository", return_value=mock_doc_repo),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await import_documentation(payload, session_id=session_id, db=MagicMock())

    assert exc_info.value.status_code == 409
    assert "duplicate key" in str(exc_info.value.detail).lower()
