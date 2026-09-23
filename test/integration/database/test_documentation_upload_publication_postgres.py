# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Upload publication must be atomic and owned by the most recently queued job."""

import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.core.errors import ExecutionOwnershipLostError
from src.core.job_execution import JobExecutionContext, reset_current_execution, set_current_execution
from src.database.models import Base, Job, Session
from src.database.repositories.documentation_repository import DocumentationRepository
from src.database.repositories.session_repository import SessionRepository
from src.documents.errors import DocumentationUploadSupersededError
from src.documents.processing.persistence import publish_uploaded_documentation


@pytest_asyncio.fixture
async def upload_database(monkeypatch):
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration tests")
    schema_name = f"test_upload_publication_{uuid4().hex}"
    engine = create_async_engine(database_url, execution_options={"schema_translate_map": {None: schema_name}})
    created = False
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
            created = True
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr("src.documents.processing.persistence.async_session_maker", factory)
        session_id, doc_id, other_doc_id, job_id, newer_job_id = (uuid4() for _ in range(5))
        pointer = f"documentation.processUpload_{doc_id}_job_id"
        async with factory() as db:
            db.add(Session(session_id=session_id))
            await db.flush()
            db.add_all(
                [
                    Job(
                        job_id=value,
                        session_id=session_id,
                        job_type="documentation.processUpload",
                        status="running",
                        input={},
                    )
                    for value in (job_id, newer_job_id)
                ]
            )
            repo = DocumentationRepository(db)
            await repo.create_documentation_item(
                session_id, "upload", "old", doc_id=doc_id, metadata={"filename": "old.md", "chunk_number": 0}
            )
            await repo.create_documentation_item(session_id, "upload", "unrelated", doc_id=other_doc_id)
            await SessionRepository(db).update_session(session_id, {pointer: str(job_id)})
            await db.commit()
            before = await repo.get_documentation_items_by_doc_id(session_id, doc_id)
            other_before = await repo.get_documentation_items_by_doc_id(session_id, other_doc_id)
        yield SimpleNamespace(
            factory=factory,
            session_id=session_id,
            doc_id=doc_id,
            job_id=job_id,
            newer_job_id=newer_job_id,
            pointer=pointer,
            before=before,
            other_doc_id=other_doc_id,
            other_before=other_before,
        )
    finally:
        if created:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        await engine.dispose()


def publication(case, *, job_id=None):
    return dict(
        session_id=case.session_id,
        doc_id=case.doc_id,
        job_id=job_id or case.job_id,
        filename="new.md",
        chunks=[
            {"content": f"new-{i}", "summary": "new summary", "metadata": {"filename": "new.md", "chunk_number": i}}
            for i in range(2)
        ],
    )


async def document(case):
    async with case.factory() as db:
        return await DocumentationRepository(db).get_documentation_items_by_doc_id(case.session_id, case.doc_id)


@pytest.mark.asyncio
async def test_publication_replaces_once_and_preserves_other_documents(upload_database):
    case = upload_database
    await publish_uploaded_documentation(**publication(case))
    # A retry of the same job must not accumulate a second set of chunks.
    await publish_uploaded_documentation(**publication(case))
    rows = await document(case)
    assert sorted(row["content"] for row in rows) == ["new-0", "new-1"]
    assert all(row["metadata"]["filename"] == "new.md" for row in rows)
    assert all(row["scrapeJobIds"] == [str(case.job_id)] for row in rows)
    async with case.factory() as db:
        assert (
            await DocumentationRepository(db).get_documentation_items_by_doc_id(case.session_id, case.other_doc_id)
            == case.other_before
        )


@pytest.mark.asyncio
async def test_failed_publication_rolls_back_deletion_and_partial_inserts(upload_database, monkeypatch):
    case = upload_database
    create_item = DocumentationRepository.create_documentation_item
    calls = 0

    async def fail_second_insert(self, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("write failed")
        return await create_item(self, **kwargs)

    monkeypatch.setattr(DocumentationRepository, "create_documentation_item", fail_second_insert)
    with pytest.raises(RuntimeError, match="write failed"):
        await publish_uploaded_documentation(**publication(case))
    assert calls == 2
    assert await document(case) == case.before


@pytest.mark.asyncio
async def test_readers_see_previous_version_until_commit(upload_database):
    case = upload_database
    async with case.factory() as db:
        assert await DocumentationRepository(db).replace_uploaded_documentation(**publication(case))
        assert await document(case) == case.before
        await db.commit()
    assert sorted(row["content"] for row in await document(case)) == ["new-0", "new-1"]


@pytest.mark.asyncio
async def test_newer_upload_pointer_blocks_older_publication(upload_database):
    case = upload_database
    async with case.factory() as db:
        await SessionRepository(db).update_session(case.session_id, {case.pointer: str(case.newer_job_id)})
        await db.commit()
    with pytest.raises(DocumentationUploadSupersededError):
        await publish_uploaded_documentation(**publication(case))
    assert await document(case) == case.before
    await publish_uploaded_documentation(**publication(case, job_id=case.newer_job_id))
    assert all(row["scrapeJobIds"] == [str(case.newer_job_id)] for row in await document(case))


@pytest.mark.asyncio
async def test_scheduling_serializes_with_publication(upload_database):
    case = upload_database
    started = asyncio.Event()

    async def schedule_newer_upload():
        async with case.factory() as db:
            started.set()
            await SessionRepository(db).update_session(case.session_id, {case.pointer: str(case.newer_job_id)})
            await db.commit()

    async with case.factory() as db:
        assert await DocumentationRepository(db).replace_uploaded_documentation(**publication(case))
        task = asyncio.create_task(schedule_newer_upload())
        try:
            await started.wait()
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), timeout=0.1)
            await db.commit()
            await asyncio.wait_for(task, timeout=5)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    with pytest.raises(DocumentationUploadSupersededError):
        await publish_uploaded_documentation(**publication(case))


@pytest.mark.asyncio
async def test_lost_execution_claim_cannot_replace_existing_document(upload_database):
    case = upload_database
    token = set_current_execution(
        JobExecutionContext(
            job_id=case.job_id,
            session_id=case.session_id,
            job_type="documentation.processUpload",
            worker_id="expired-worker",
            execution_token=uuid4(),
        )
    )
    try:
        with pytest.raises(ExecutionOwnershipLostError):
            await publish_uploaded_documentation(**publication(case))
    finally:
        reset_current_execution(token)
    assert await document(case) == case.before
