# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Cache identity and reuse of attribute jobs that store their documentation selection (PostgreSQL)."""

import os
from datetime import timedelta
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.database.models import Base, Job, Session
from src.database.repositories.job_repository import JobRepository
from src.documents.selection import DocumentationSelection, SelectionRole
from src.jobs.cache import reuse_or_run
from src.shared.clock import utc_now
from src.shared.job_types import JobType, job_type_policy
from src.shared.json_values import to_jsonable
from src.shared.normalize import DOCUMENTATION_SELECTION_INPUT_KEY, normalized_input_fingerprint

SessionFactory = async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def postgres_session_factory() -> AsyncIterator[SessionFactory]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL repository integration tests")

    schema_name = f"test_selection_cache_{uuid4().hex}"
    engine = create_async_engine(database_url, execution_options={"schema_translate_map": {None: schema_name}})
    schema_created = False
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
            schema_created = True
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        if schema_created:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        await engine.dispose()


def _item(content: str) -> dict[str, Any]:
    return {
        "chunkId": str(uuid4()),
        "docId": str(uuid4()),
        "url": f"https://docs.example.com/{content.split()[0].lower()}",
        "summary": content,
        "content": content,
        "@metadata": {"category": "spec_yaml", "tags": ["user"], "token_count": 3},
    }


def _renumbered(item: dict[str, Any]) -> dict[str, Any]:
    """The same documentation as stored by another session: identical text, new UUIDs."""
    return {**item, "chunkId": str(uuid4()), "docId": str(uuid4())}


def _job_input(corpus: list[dict[str, Any]], primary: dict, fallback: dict, *, api_type: str = "rest") -> dict:
    selection = DocumentationSelection.from_corpus(
        corpus, {SelectionRole.PRIMARY: [primary], SelectionRole.FALLBACK: [fallback]}
    )
    return {
        "objectClass": "user",
        "apiType": api_type,
        DOCUMENTATION_SELECTION_INPUT_KEY: selection.to_job_input(),
        "skipCache": False,
    }


async def _store_finished_job(factory: SessionFactory, job_input: dict, result: dict) -> UUID:
    session_id, job_id = uuid4(), uuid4()
    json_input = to_jsonable(job_input)
    async with factory() as db:
        db.add(Session(session_id=session_id))
        db.add(
            Job(
                job_id=job_id,
                session_id=session_id,
                job_type=JobType.DIGESTER_ATTRIBUTES.value,
                status="finished",
                input=json_input,
                normalized_input_hash=normalized_input_fingerprint(json_input),
                result=result,
            )
        )
        await db.commit()
    return job_id


async def _new_session(factory: SessionFactory) -> UUID:
    session_id = uuid4()
    async with factory() as db:
        db.add(Session(session_id=session_id))
        await db.commit()
    return session_id


async def _find(factory: SessionFactory, job_input: dict, session_id: UUID):
    async with factory() as db:
        return await JobRepository(db).get_job_by_input(
            JobType.DIGESTER_ATTRIBUTES,
            job_input,
            utc_now() - timedelta(days=1),
            requesting_session_id=session_id,
        )


@pytest.mark.asyncio
async def test_identity_follows_the_stored_selection_not_the_session_corpus(
    postgres_session_factory: SessionFactory,
) -> None:
    primary, fallback, unrelated = _item("User overview"), _item("User reference"), _item("Pricing page")
    source_job_id = await _store_finished_job(
        postgres_session_factory, _job_input([primary, fallback], primary, fallback), {"result": {}}
    )
    requesting_session = await _new_session(postgres_session_factory)
    copied_primary, copied_fallback = _renumbered(primary), _renumbered(fallback)

    # Same selected documentation under other UUIDs, next to a document outside the selection: a hit.
    hit = await _find(
        postgres_session_factory,
        _job_input([_renumbered(unrelated), copied_primary, copied_fallback], copied_primary, copied_fallback),
        requesting_session,
    )
    assert hit is not None and hit.job_id == source_job_id

    # Another protocol, or the same chunks in different attempts, is a different job.
    other_protocol = _job_input([copied_primary, copied_fallback], copied_primary, copied_fallback, api_type="scim")
    swapped_roles = _job_input([copied_primary, copied_fallback], copied_fallback, copied_primary)
    revised_fallback = {**copied_fallback, "content": "User reference, revised"}
    changed_fallback = _job_input([copied_primary, revised_fallback], copied_primary, revised_fallback)
    for job_input in (other_protocol, swapped_roles, changed_fallback):
        assert await _find(postgres_session_factory, job_input, requesting_session) is None


@pytest.mark.asyncio
async def test_reuse_remaps_relevance_from_the_source_input_even_after_its_document_was_replaced(
    postgres_session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary, fallback = _item("User overview"), _item("User reference")
    cached_result = {
        "result": {
            "attributes": {
                "email": {
                    "type": "string",
                    "relevantDocumentations": [{"docId": fallback["docId"], "chunkId": fallback["chunkId"]}],
                }
            }
        },
        "relevantDocumentations": [{"doc_id": primary["docId"], "chunk_id": primary["chunkId"]}],
    }
    # The source session's documentation rows never existed here: the source job's input is
    # the only record of what it read, exactly as after the source document was replaced.
    await _store_finished_job(
        postgres_session_factory, _job_input([primary, fallback], primary, fallback), cached_result
    )
    requesting_session = await _new_session(postgres_session_factory)
    copied_primary, copied_fallback = _renumbered(primary), _renumbered(fallback)
    worker = AsyncMock(return_value={"fresh": True})
    monkeypatch.setattr("src.jobs.cache.async_session_maker", postgres_session_factory)
    monkeypatch.setattr("src.jobs.cache.lifecycle.update_job_progress", AsyncMock())

    cache_policy = job_type_policy(JobType.DIGESTER_ATTRIBUTES).cache
    assert cache_policy is not None
    result = await reuse_or_run(
        job_type=JobType.DIGESTER_ATTRIBUTES,
        cache_policy=cache_policy,
        job_id=uuid4(),
        session_id=requesting_session,
        input_payload=_job_input([copied_primary, copied_fallback], copied_primary, copied_fallback),
        run_normal_worker=worker,
    )

    worker.assert_not_awaited()
    assert result["relevantDocumentations"] == [
        {"doc_id": copied_primary["docId"], "chunk_id": copied_primary["chunkId"]}
    ]
    assert result["result"]["attributes"]["email"]["relevantDocumentations"] == [
        {"docId": copied_fallback["docId"], "chunkId": copied_fallback["chunkId"]}
    ]


@pytest.mark.asyncio
async def test_a_job_stored_by_the_previous_milestone_is_never_a_cache_hit(
    postgres_session_factory: SessionFactory,
) -> None:
    """Rows written before the stored selection carried the whole corpus; they must miss, not crash."""
    primary, fallback = _item("User overview"), _item("User reference")
    previous_milestone_input = {
        "documentationItems": [primary, fallback],
        "objectClass": "user",
        "relevantDocumentations": [{"doc_id": primary["docId"], "chunk_id": primary["chunkId"]}],
        "skipCache": False,
    }
    await _store_finished_job(postgres_session_factory, previous_milestone_input, {"result": {}})
    requesting_session = await _new_session(postgres_session_factory)

    assert (
        await _find(postgres_session_factory, _job_input([primary, fallback], primary, fallback), requesting_session)
        is None
    )
