# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.database.models import ApiKey, Base, Job, Session
from src.database.repositories.job_repository import JobRepository
from src.shared.normalize import normalized_input_fingerprint


@pytest.mark.asyncio
async def test_job_lookup_enforces_session_and_tenant_boundaries() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL repository integration tests")

    schema_name = f"test_job_repository_{uuid4().hex}"
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

        owner_a_id = uuid4()
        owner_b_id = uuid4()
        owner_a_source_session_id = uuid4()
        owner_a_requesting_session_id = uuid4()
        owner_b_session_id = uuid4()
        ownerless_source_session_id = uuid4()
        ownerless_requesting_session_id = uuid4()
        owner_a_job_id = uuid4()
        owner_b_job_id = uuid4()
        ownerless_job_id = uuid4()
        job_type = "discovery.getCandidateLinks"
        normalized_input = {"applicationName": "Demo"}
        now = datetime.now(timezone.utc)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with session_factory() as db:
            db.add_all(
                [
                    ApiKey(
                        api_key_id=owner_a_id,
                        name="owner-a",
                        key_prefix="mpcg_a",
                        key_hash="a" * 64,
                    ),
                    ApiKey(
                        api_key_id=owner_b_id,
                        name="owner-b",
                        key_prefix="mpcg_b",
                        key_hash="b" * 64,
                    ),
                    Session(session_id=owner_a_source_session_id, api_key_id=owner_a_id),
                    Session(session_id=owner_a_requesting_session_id, api_key_id=owner_a_id),
                    Session(session_id=owner_b_session_id, api_key_id=owner_b_id),
                    Session(session_id=ownerless_source_session_id),
                    Session(session_id=ownerless_requesting_session_id),
                    Job(
                        job_id=owner_a_job_id,
                        session_id=owner_a_source_session_id,
                        job_type=job_type,
                        status="finished",
                        input=normalized_input,
                        normalized_input=normalized_input_fingerprint(normalized_input),
                        result={"owner": "a"},
                        created_at=now,
                    ),
                    Job(
                        job_id=owner_b_job_id,
                        session_id=owner_b_session_id,
                        job_type=job_type,
                        status="finished",
                        input=normalized_input,
                        normalized_input=normalized_input_fingerprint(normalized_input),
                        result={"owner": "b"},
                        created_at=now + timedelta(seconds=1),
                    ),
                    Job(
                        job_id=ownerless_job_id,
                        session_id=ownerless_source_session_id,
                        job_type=job_type,
                        status="finished",
                        input=normalized_input,
                        normalized_input=normalized_input_fingerprint(normalized_input),
                        result={"owner": None},
                        created_at=now + timedelta(seconds=2),
                    ),
                ]
            )
            await db.commit()

            repo = JobRepository(db)
            owner_a_match = await repo.get_job_by_input(
                job_type,
                normalized_input,
                now - timedelta(days=1),
                requesting_session_id=owner_a_requesting_session_id,
            )
            owner_b_match = await repo.get_job_by_input(
                job_type,
                normalized_input,
                now - timedelta(days=1),
                requesting_session_id=owner_b_session_id,
            )
            ownerless_match = await repo.get_job_by_input(
                job_type,
                normalized_input,
                now - timedelta(days=1),
                requesting_session_id=ownerless_source_session_id,
            )
            other_ownerless_match = await repo.get_job_by_input(
                job_type,
                normalized_input,
                now - timedelta(days=1),
                requesting_session_id=ownerless_requesting_session_id,
            )

            assert owner_a_match is not None and owner_a_match.job_id == owner_a_job_id
            assert owner_b_match is not None and owner_b_match.job_id == owner_b_job_id
            assert ownerless_match is not None and ownerless_match.job_id == ownerless_job_id
            # Ownerless sessions form one tenant: a different ownerless session
            # reuses the ownerless job even though it did not create it.
            assert other_ownerless_match is not None and other_ownerless_match.job_id == ownerless_job_id
            assert await repo.get_job_for_session(owner_a_job_id, owner_a_source_session_id) is not None
            assert await repo.get_job_for_session(owner_a_job_id, owner_a_requesting_session_id) is None
    finally:
        if schema_created:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        await engine.dispose()
