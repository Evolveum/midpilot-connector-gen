# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.database.models import Base, Document, DocumentationChunk, Session
from src.database.repositories.relevant_chunk_repository import RelevantChunkRepository


@pytest.mark.asyncio
async def test_relevant_chunks_are_stored_against_the_document_the_chunk_belongs_to():
    """Relevance references come from an LLM, so their document id is only a claim.

    The stored document must win over the claimed one, references to chunks
    outside the session must be dropped instead of persisted, and the composite
    foreign key must reject anything that would still be inconsistent.
    """
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL repository integration tests")

    schema_name = f"test_relevant_chunks_{uuid4().hex}"
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
        doc_id = uuid4()
        claimed_doc_id = uuid4()
        other_doc_id = uuid4()
        chunk_id = uuid4()
        unknown_chunk_id = uuid4()
        other_session_chunk_id = uuid4()
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with session_factory() as db:
            db.add_all(
                [
                    Session(session_id=session_id),
                    Session(session_id=other_session_id),
                    Document(session_id=session_id, doc_id=doc_id, source="upload"),
                    Document(session_id=other_session_id, doc_id=other_doc_id, source="upload"),
                    DocumentationChunk(
                        chunk_id=chunk_id,
                        session_id=session_id,
                        doc_id=doc_id,
                        content="in-session",
                    ),
                    DocumentationChunk(
                        chunk_id=other_session_chunk_id,
                        session_id=other_session_id,
                        doc_id=other_doc_id,
                        content="other-session",
                    ),
                ]
            )
            await db.commit()

            repository = RelevantChunkRepository(db)
            stored = await repository.replace_relevant_chunks_for_result(
                session_id=session_id,
                result_key="objectClassesOutput",
                chunks=[
                    {"docId": str(claimed_doc_id), "chunkId": str(chunk_id), "entityKey": "account"},
                    {
                        "docId": str(other_doc_id),
                        "chunkId": str(other_session_chunk_id),
                        "entityKey": "foreign",
                    },
                    {
                        "docId": str(doc_id),
                        "chunkId": str(unknown_chunk_id),
                        "entityKey": "orphan",
                    },
                ],
            )
            await db.commit()

            assert stored == 1

            persisted = await repository.get_relevant_chunks(session_id=session_id)

        assert persisted == [
            {
                "resultKey": "objectClassesOutput",
                "docId": str(doc_id),
                "chunkId": str(chunk_id),
                "entityKey": "account",
            }
        ]

        async with session_factory() as db:
            await db.execute(delete(DocumentationChunk).where(DocumentationChunk.chunk_id == chunk_id))
            await db.commit()

            remaining = await RelevantChunkRepository(db).count_by_session(session_id)

        assert remaining == 0
    finally:
        if schema_created:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_long_llm_anchor_does_not_abort_the_extraction_write():
    """A btree index tuple is capped at 2704 bytes.

    ``start_sequence``/``end_sequence`` are unbounded LLM output, so indexing the
    raw JSONB made a long enough anchor fail the INSERT and lose every relevant
    chunk for that result key. The uniqueness rule must still hold on the value.
    """
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL repository integration tests")

    schema_name = f"test_relevant_chunk_anchor_{uuid4().hex}"
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
        doc_id = uuid4()
        chunk_id = uuid4()
        long_anchor = uuid4().hex * 200
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with session_factory() as db:
            db.add_all(
                [
                    Session(session_id=session_id),
                    Document(session_id=session_id, doc_id=doc_id, source="upload"),
                    DocumentationChunk(
                        chunk_id=chunk_id,
                        session_id=session_id,
                        doc_id=doc_id,
                        content="chunk",
                    ),
                ]
            )
            await db.commit()

            repository = RelevantChunkRepository(db)
            stored = await repository.replace_relevant_chunks_for_result(
                session_id=session_id,
                result_key="objectClassesOutput",
                chunks=[
                    {
                        "chunkId": str(chunk_id),
                        "entityKey": "account",
                        "relevantSequence": {"startSequence": long_anchor, "endSequence": long_anchor},
                    },
                    {
                        "chunkId": str(chunk_id),
                        "entityKey": "account",
                        "relevantSequence": {"startSequence": "start", "endSequence": "end"},
                    },
                    {
                        "chunkId": str(chunk_id),
                        "entityKey": "account",
                        "relevantSequence": {"startSequence": long_anchor, "endSequence": long_anchor},
                    },
                ],
            )
            await db.commit()

            assert stored == 2

            persisted = await repository.get_relevant_chunks(session_id=session_id)

        assert len(persisted) == 2
        assert any(item.get("relevantSequence", {}).get("startSequence") == long_anchor for item in persisted)
    finally:
        if schema_created:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        await engine.dispose()
