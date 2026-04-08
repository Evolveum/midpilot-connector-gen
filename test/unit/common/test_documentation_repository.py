# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.common.database.repositories.documentation_repository import DocumentationRepository


def _build_repo() -> tuple[DocumentationRepository, MagicMock]:
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return DocumentationRepository(db), db


@pytest.mark.asyncio
async def test_import_documentation_items_for_session_preserves_exported_values() -> None:
    repo, db = _build_repo()
    session_id = uuid4()
    doc_id = uuid4()
    chunk_id = uuid4()

    items = [
        {
            "chunkId": str(chunk_id),
            "docId": str(doc_id),
            "source": "upload",
            "url": "upload://connector-openapi.json",
            "summary": "Chunk summary",
            "content": "Chunk full content",
            "metadata": {"category": "reference", "length": 123},
            "createdAt": "2026-04-02T12:34:56Z",
            "scrapeJobIds": ["job-1", "job-2"],
        }
    ]

    imported_count = await repo.import_documentation_items_for_session(session_id, items)

    assert imported_count == 1
    db.flush.assert_awaited_once()
    db.add.assert_called_once()

    added_item = db.add.call_args.args[0]
    assert added_item.session_id == session_id
    assert added_item.doc_id == doc_id
    assert added_item.chunk_id == chunk_id
    assert added_item.source == "upload"
    assert added_item.url == "upload://connector-openapi.json"
    assert added_item.summary == "Chunk summary"
    assert added_item.content == "Chunk full content"
    assert added_item.doc_metadata == {"category": "reference", "length": 123}
    assert list(added_item.scrape_job_ids) == ["job-1", "job-2"]
    assert added_item.created_at.isoformat() == "2026-04-02T12:34:56+00:00"
