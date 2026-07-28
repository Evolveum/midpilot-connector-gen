# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.session.errors import InvalidDocumentationImportError
from src.session.routes import documentation
from src.session.schema import Documentation


def _chunk_payload(chunk_id) -> dict:
    return {
        "chunkId": str(chunk_id),
        "source": "upload",
        "content": "content",
        "createdAt": "2026-01-01T00:00:00Z",
    }


def _duplicate_chunks_payload(_documentation_id) -> dict:
    chunk_id = uuid4()
    return {"chunks": [_chunk_payload(chunk_id), _chunk_payload(chunk_id)]}


@pytest.mark.parametrize(
    ("payload_factory", "expected_message"),
    [
        (
            lambda documentation_id: {"docId": str(uuid4()), "chunks": [_chunk_payload(uuid4())]},
            "must match path documentation_id",
        ),
        (
            _duplicate_chunks_payload,
            "Duplicate chunkId in import payload",
        ),
        (
            lambda documentation_id: {"schemas": [{"name": "User"}]},
            "non-empty 'chunks' field",
        ),
        (
            lambda documentation_id: {"chunks": []},
            "Import payload must include at least one documentation chunk.",
        ),
    ],
    ids=["mismatched-doc-id", "duplicate-chunk-id", "missing-chunks", "empty-chunks"],
)
@pytest.mark.asyncio
async def test_import_documentation_raises_domain_error_before_deleting_existing_chunks(
    payload_factory, expected_message: str
) -> None:
    session_id = uuid4()
    documentation_id = uuid4()
    document = Documentation.model_validate(payload_factory(documentation_id))

    session_repo = MagicMock()
    session_repo.session_exists = AsyncMock(return_value=True)
    doc_repo = MagicMock()
    doc_repo.remove_documentation_items_by_doc_id = AsyncMock()
    doc_repo.import_documentation_items_for_session = AsyncMock()

    with (
        patch("src.session.routes.documentation.SessionRepository", return_value=session_repo),
        patch("src.session.routes.documentation.DocumentationRepository", return_value=doc_repo),
        pytest.raises(InvalidDocumentationImportError) as exc_info,
    ):
        await documentation.import_documentation_by_id(document, session_id, documentation_id, db=MagicMock())

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "invalid_documentation_import"
    assert expected_message in exc_info.value.message
    doc_repo.remove_documentation_items_by_doc_id.assert_not_awaited()
    doc_repo.import_documentation_items_for_session.assert_not_awaited()
