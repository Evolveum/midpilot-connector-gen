# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.database.models import Document, DocumentationChunk, RelevantChunk, Session
from src.database.repositories.relevant_chunk_repository import RelevantChunkRepository


@pytest.mark.asyncio
async def test_relevant_chunks_are_stored_against_the_document_the_chunk_belongs_to(
    postgres_session_factory: async_sessionmaker[AsyncSession],
):
    """Relevance references come from an LLM, so their document id is only a claim.

    The stored document must win over the claimed one, references to chunks
    outside the session must be dropped instead of persisted, and the composite
    foreign key must reject anything that would still be inconsistent.
    """
    session_id = uuid4()
    other_session_id = uuid4()
    doc_id = uuid4()
    claimed_doc_id = uuid4()
    other_doc_id = uuid4()
    chunk_id = uuid4()
    unknown_chunk_id = uuid4()
    other_session_chunk_id = uuid4()

    async with postgres_session_factory() as db:
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

    async with postgres_session_factory() as db:
        await db.execute(delete(DocumentationChunk).where(DocumentationChunk.chunk_id == chunk_id))
        await db.commit()

        remaining = int(
            (
                await db.execute(
                    select(func.count()).select_from(RelevantChunk).where(RelevantChunk.session_id == session_id)
                )
            ).scalar_one()
        )

    assert remaining == 0


@pytest.mark.asyncio
async def test_a_long_llm_anchor_does_not_abort_the_extraction_write(
    postgres_session_factory: async_sessionmaker[AsyncSession],
):
    """A btree index tuple is capped at 2704 bytes.

    ``start_sequence``/``end_sequence`` are unbounded LLM output, so indexing the
    raw JSONB made a long enough anchor fail the INSERT and lose every relevant
    chunk for that result key. The uniqueness rule must still hold on the value.
    """
    session_id = uuid4()
    doc_id = uuid4()
    chunk_id = uuid4()
    long_anchor = uuid4().hex * 200

    async with postgres_session_factory() as db:
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
