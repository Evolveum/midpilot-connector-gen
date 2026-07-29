# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from src.core.errors import ExecutionOwnershipLostError
from src.core.job_execution import JobExecutionContext
from src.database.repositories.documentation_repository import DocumentationRepository, DocumentationWriteBatch


def _build_repo() -> tuple[DocumentationRepository, MagicMock]:
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return DocumentationRepository(db), db


@pytest.mark.asyncio
async def test_get_conndev_documentation_items_builds_normalized_postgres_filter() -> None:
    repo, db = _build_repo()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result)

    await repo.get_conndev_documentation_items_by_session(uuid4())

    query = db.execute.await_args.args[0]
    compiled = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    assert "lower(btrim(split_part((documentation_items.metadata ->> 'content_type'), ';', 1)))" in compiled
    assert "application/com.evolveum.conndev+json" in compiled
    assert "application/conndev+json" in compiled
    assert "ORDER BY documentation_items.created_at" in compiled


@pytest.mark.asyncio
async def test_get_scraped_documentation_items_for_export_by_origin_job_filters_by_creator() -> None:
    repo, db = _build_repo()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result)
    session_id = uuid4()
    job_id = uuid4()

    assert await repo.get_scraped_documentation_items_for_export_by_origin_job(session_id, job_id) == []

    query = db.execute.await_args.args[0]
    compiled = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    assert f"documentation_items.session_id = '{session_id}'" in compiled
    assert f"documentation_items.origin_job_id = '{job_id}'" in compiled
    assert "documentation_items.source = 'scraper'" in compiled
    assert "scrape_job_ids @>" not in compiled
    assert (
        "ORDER BY documentation_items.doc_id, documentation_items.created_at, documentation_items.chunk_id" in compiled
    )


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
            "metadata": {"category": "reference", "token_count": 123},
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
    assert added_item.doc_metadata == {"category": "reference", "token_count": 123}
    assert list(added_item.scrape_job_ids) == ["job-1", "job-2"]
    assert added_item.created_at.isoformat() == "2026-04-02T12:34:56+00:00"


@pytest.mark.asyncio
async def test_idempotent_chunk_upsert_preserves_other_job_links() -> None:
    repo, db = _build_repo()
    result = MagicMock()
    result.scalar_one.return_value = uuid4()
    db.execute = AsyncMock(return_value=result)

    await repo.create_documentation_item(
        session_id=uuid4(),
        source="scraper",
        content="same content",
        original_job_id=uuid4(),
        url="https://example.test/docs",
        metadata={"chunk_number": 0},
    )

    statement = db.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "CASE WHEN" in sql
    assert "scrape_job_ids" in sql
    assert "||" in sql


@pytest.mark.asyncio
async def test_execution_fence_is_acquired_once_per_transaction() -> None:
    repo, db = _build_repo()
    transaction = object()
    db.get_transaction = MagicMock(side_effect=[None, transaction, transaction])
    execution = JobExecutionContext(job_id=uuid4(), worker_id="worker-a", execution_token=uuid4())

    with (
        patch(
            "src.database.repositories.documentation_repository.get_current_execution",
            return_value=execution,
        ),
        patch(
            "src.database.repositories.documentation_repository.JobRepository.acquire_execution_fence",
            new_callable=AsyncMock,
        ) as acquire_fence,
    ):
        await repo._assert_current_execution(execution.job_id)
        await repo._assert_current_execution(execution.job_id)

    acquire_fence.assert_awaited_once_with(
        execution.job_id,
        worker_id=execution.worker_id,
        execution_token=execution.execution_token,
    )


@pytest.mark.asyncio
async def test_execution_fence_rejects_a_different_ambient_job() -> None:
    repo, _ = _build_repo()
    execution = JobExecutionContext(job_id=uuid4(), worker_id="worker-a", execution_token=uuid4())

    with patch(
        "src.database.repositories.documentation_repository.get_current_execution",
        return_value=execution,
    ):
        with pytest.raises(ExecutionOwnershipLostError):
            await repo._assert_current_execution(uuid4())


@pytest.mark.asyncio
async def test_documentation_write_batch_commits_at_limit_and_flushes_remainder() -> None:
    db = MagicMock()
    db.commit = AsyncMock()
    batch = DocumentationWriteBatch(db, batch_size=2)

    await batch.record_write()
    db.commit.assert_not_awaited()
    await batch.record_write()
    db.commit.assert_awaited_once()

    await batch.record_write()
    await batch.commit_pending()
    assert db.commit.await_count == 2
