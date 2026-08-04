# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.database.models import Base, Document, DocumentationChunk, Session
from src.database.repositories.documentation_repository import DocumentationRepository


@pytest.mark.asyncio
async def test_get_conndev_documentation_items_matches_python_content_type_normalization():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL repository integration tests")

    schema_name = f"test_conndev_repository_{uuid4().hex}"
    engine = create_async_engine(
        database_url,
        execution_options={"schema_translate_map": {None: schema_name}},
    )
    schema_created = False

    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
            schema_created = True
            await connection.run_sync(Base.metadata.create_all)

        session_id = uuid4()
        other_session_id = uuid4()
        first_created_at = datetime.now(timezone.utc)
        second_created_at = first_created_at + timedelta(seconds=1)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with session_factory() as db:

            def document(content_type, *, session=session_id):
                doc_id = uuid4()
                return Document(
                    session_id=session,
                    doc_id=doc_id,
                    source="upload",
                    content_type=content_type,
                )

            conndev_with_charset = document(" APPLICATION/CONNDEV+JSON; charset=utf-8 ")
            conndev_evolveum = document("application/com.evolveum.conndev+json")
            ordinary_json = document("application/json")
            without_content_type = document(None)
            other_session_conndev = document("application/conndev+json", session=other_session_id)

            def chunk(doc, content, created_at=None):
                kwargs = {
                    "session_id": doc.session_id,
                    "doc_id": doc.doc_id,
                    "content": content,
                }
                if created_at is not None:
                    kwargs["created_at"] = created_at
                return DocumentationChunk(**kwargs)

            db.add_all(
                [
                    Session(session_id=session_id),
                    Session(session_id=other_session_id),
                    conndev_with_charset,
                    conndev_evolveum,
                    ordinary_json,
                    without_content_type,
                    other_session_conndev,
                    chunk(conndev_with_charset, "first", first_created_at),
                    chunk(conndev_evolveum, "second", second_created_at),
                    chunk(ordinary_json, "ordinary-json"),
                    chunk(without_content_type, "missing-content-type"),
                    chunk(other_session_conndev, "other-session"),
                ]
            )
            await db.commit()

            items = await DocumentationRepository(db).get_conndev_documentation_items_by_session(session_id)

        assert [item["content"] for item in items] == ["first", "second"]
    finally:
        if schema_created:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        await engine.dispose()


@pytest.mark.asyncio
async def test_update_documentation_item_creates_target_document_before_moving_chunk():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL repository integration tests")

    schema_name = f"test_documentation_update_{uuid4().hex}"
    engine = create_async_engine(
        database_url,
        execution_options={"schema_translate_map": {None: schema_name}},
    )
    schema_created = False

    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
            schema_created = True
            await connection.run_sync(Base.metadata.create_all)

        session_id = uuid4()
        original_doc_id = uuid4()
        target_doc_id = uuid4()
        chunk_id = uuid4()
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with session_factory() as db:
            db.add_all(
                [
                    Session(session_id=session_id),
                    Document(
                        session_id=session_id,
                        doc_id=original_doc_id,
                        source="upload",
                        url="upload://original.json",
                    ),
                    DocumentationChunk(
                        session_id=session_id,
                        doc_id=original_doc_id,
                        chunk_id=chunk_id,
                        content="original content",
                    ),
                ]
            )
            await db.commit()

            updated = await DocumentationRepository(db).update_documentation_item(
                chunk_id,
                doc_id=target_doc_id,
            )
            await db.commit()

            moved_chunk = await db.get(DocumentationChunk, chunk_id)
            target_document = await db.get(Document, (session_id, target_doc_id))

        assert updated is True
        assert moved_chunk is not None
        assert moved_chunk.doc_id == target_doc_id
        assert target_document is not None
        assert target_document.source == "upload"
        assert target_document.url == "upload://original.json"
    finally:
        if schema_created:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        await engine.dispose()
