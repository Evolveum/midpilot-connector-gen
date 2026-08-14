# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.auth.keys import generate_api_key
from src.database.repositories.api_key_repository import ApiKeyRepository
from src.database.repositories.session_repository import SessionRepository


@pytest.mark.asyncio
async def test_api_key_lifecycle_and_session_ownership(
    postgres_session_factory: async_sessionmaker[AsyncSession],
):
    async with postgres_session_factory() as db:
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
