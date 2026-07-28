# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.auth.keys import generate_api_key
from src.database.models import Base
from src.database.repositories.api_key_repository import ApiKeyRepository
from src.database.repositories.session_repository import SessionRepository


@pytest.mark.asyncio
async def test_api_key_lifecycle_and_session_ownership():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL repository integration tests")

    schema_name = f"test_api_key_repository_{uuid4().hex}"
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
            key_repo = ApiKeyRepository(db)
            session_repo = SessionRepository(db)

            # Issue a key and authenticate by hash.
            generated = generate_api_key()
            record = await key_repo.create_api_key(
                name="integration test key", key_prefix=generated.prefix, key_hash=generated.hash
            )
            assert await key_repo.get_active_key_by_hash(generated.hash) is not None

            # Session ownership round-trip.
            owned_session_id = await session_repo.create_session(api_key_id=record.api_key_id)
            ownerless_session_id = await session_repo.create_session()

            owned = await session_repo.get_session_owner(owned_session_id)
            assert owned is not None and owned.api_key_id == record.api_key_id
            ownerless = await session_repo.get_session_owner(ownerless_session_id)
            assert ownerless is not None and ownerless.api_key_id is None
            assert await session_repo.get_session_owner(uuid4()) is None

            # Revocation: key stops resolving, record and ownership stay for audit.
            revoked = await key_repo.revoke_api_key(record.api_key_id)
            assert revoked is not None and revoked.revoked_at is not None
            assert await key_repo.get_active_key_by_hash(generated.hash) is None

            still_owned = await session_repo.get_session_owner(owned_session_id)
            assert still_owned is not None and still_owned.api_key_id == record.api_key_id

            # Revoking again is idempotent and keeps the original timestamp.
            revoked_again = await key_repo.revoke_api_key(record.api_key_id)
            assert revoked_again is not None and revoked_again.revoked_at == revoked.revoked_at

            # Unknown key ID reports not found.
            assert await key_repo.revoke_api_key(uuid4()) is None

            listed = await key_repo.list_api_keys()
            assert [k.api_key_id for k in listed] == [record.api_key_id]

            await db.commit()
    finally:
        if schema_created:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema_name}" CASCADE'))
        await engine.dispose()
