# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.auth.keys import hash_api_key
from src.database.models import Base
from src.database.repositories.session_repository import SessionRepository


@pytest.mark.asyncio
async def test_gateway_key_session_ownership():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL repository integration tests")

    schema_name = f"test_session_ownership_{uuid4().hex}"
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

        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with session_factory() as db:
            session_repo = SessionRepository(db)

            fingerprint = hash_api_key("gateway-generated-test-key")
            owned_session_id = await session_repo.create_session(owner_key_hash=fingerprint)
            other_session_id = uuid4()
            await session_repo.create_session_with_id(other_session_id, owner_key_hash=fingerprint)
            ownerless_session_id = await session_repo.create_session()
            await db.commit()

        async with session_factory() as db:
            session_repo = SessionRepository(db)
            for session_id in (owned_session_id, other_session_id):
                owned = await session_repo.get_session_owner(session_id)
                assert owned is not None and owned.owner_key_hash == fingerprint
            ownerless = await session_repo.get_session_owner(ownerless_session_id)
            assert ownerless is not None and ownerless.owner_key_hash is None
            assert await session_repo.get_session_owner(uuid4()) is None
            assert "api_keys" not in Base.metadata.tables

            await db.commit()
    finally:
        if schema_created:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema_name}" CASCADE'))
        await engine.dispose()
