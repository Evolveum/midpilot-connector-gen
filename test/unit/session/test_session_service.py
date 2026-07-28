# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.session import service
from src.session.errors import DocumentationItemNotFoundError


@pytest.mark.asyncio
async def test_list_documentation_upload_jobs_filters_by_type():
    session_id = uuid4()
    job_repo = MagicMock()
    job_repo.get_jobs_by_session = AsyncMock(
        return_value=[
            {"jobId": "1", "type": "documentation.processUpload"},
            {"jobId": "2", "type": "documentation.processUpload_abc_job_id"},
            {"jobId": "3", "type": "codegen.getSearch"},
            {"jobId": "4"},  # missing type -> excluded
        ]
    )

    result = await service.list_documentation_upload_jobs(job_repo, session_id)

    assert [job["jobId"] for job in result] == ["1", "2"]
    job_repo.get_jobs_by_session.assert_awaited_once_with(session_id)


def _doc_row(session_doc_id: str, chunk_id: str) -> dict:
    return {
        "docId": session_doc_id,
        "chunkId": chunk_id,
        "source": "upload",
        "url": None,
        "summary": "s",
        "content": "c",
        "metadata": {},
        "createdAt": "2026-01-01T00:00:00Z",
        "scrapeJobIds": [],
    }


@pytest.mark.asyncio
async def test_get_documentation_document_returns_only_matching_doc():
    session_id = uuid4()
    doc_id = uuid4()
    other_id = uuid4()
    chunk_1, chunk_2, chunk_3 = uuid4(), uuid4(), uuid4()
    doc_repo = MagicMock()
    doc_repo.get_documentation_items_for_export = AsyncMock(
        return_value=[
            _doc_row(str(doc_id), str(chunk_1)),
            _doc_row(str(other_id), str(chunk_2)),
            _doc_row(str(doc_id), str(chunk_3)),
        ]
    )

    document = await service.get_documentation_document(doc_repo, session_id, doc_id)

    assert str(document.doc_id) == str(doc_id)
    assert {str(chunk.chunk_id) for chunk in document.chunks} == {str(chunk_1), str(chunk_3)}


@pytest.mark.asyncio
async def test_get_documentation_document_raises_when_absent():
    session_id = uuid4()
    doc_repo = MagicMock()
    doc_repo.get_documentation_items_for_export = AsyncMock(return_value=[_doc_row(str(uuid4()), str(uuid4()))])

    with pytest.raises(DocumentationItemNotFoundError):
        await service.get_documentation_document(doc_repo, session_id, uuid4())


@pytest.mark.asyncio
async def test_delete_documentation_document_detaches_jobs_and_returns_count():
    session_id = uuid4()
    doc_id = uuid4()
    doc_repo = MagicMock()
    doc_repo.get_documentation_items_by_doc_id = AsyncMock(return_value=[{"source": "upload"}])
    doc_repo.remove_job_ids_from_documentation_items = AsyncMock()
    doc_repo.remove_documentation_items_by_doc_id = AsyncMock(return_value=3)

    deleted = await service.delete_documentation_document(doc_repo, session_id, doc_id)

    assert deleted == 3
    doc_repo.remove_job_ids_from_documentation_items.assert_awaited_once_with(session_id, "upload")
    doc_repo.remove_documentation_items_by_doc_id.assert_awaited_once_with(session_id, doc_id)


@pytest.mark.asyncio
async def test_delete_documentation_document_raises_when_absent_and_skips_job_detach():
    session_id = uuid4()
    doc_repo = MagicMock()
    doc_repo.get_documentation_items_by_doc_id = AsyncMock(return_value=[])
    doc_repo.remove_job_ids_from_documentation_items = AsyncMock()
    doc_repo.remove_documentation_items_by_doc_id = AsyncMock()

    with pytest.raises(DocumentationItemNotFoundError):
        await service.delete_documentation_document(doc_repo, session_id, uuid4())

    doc_repo.remove_job_ids_from_documentation_items.assert_not_awaited()
    doc_repo.remove_documentation_items_by_doc_id.assert_not_awaited()
