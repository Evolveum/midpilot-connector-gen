# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import ApiKey

logger = logging.getLogger(__name__)


class ApiKeyRepository:
    """Repository for API key data access operations."""

    def __init__(self, db: AsyncSession):
        """
        Initialize repository with database session.

        :param db: SQLAlchemy AsyncSession
        """
        self.db = db

    async def create_api_key(self, name: str, key_prefix: str, key_hash: str) -> ApiKey:
        """
        Persist a new API key record. Only the hash of the key is stored.

        :param name: Descriptive name of the key
        :param key_prefix: First characters of the key value, for identification
        :param key_hash: SHA-256 hex digest of the full key value
        :return: The created ApiKey record
        """
        api_key = ApiKey(name=name, key_prefix=key_prefix, key_hash=key_hash)
        self.db.add(api_key)
        await self.db.flush()
        logger.info(f"Created API key {api_key.api_key_id} ({name})")
        return api_key

    async def get_active_key_by_hash(self, key_hash: str) -> Optional[ApiKey]:
        """
        Look up a non-revoked API key by the hash of its value.

        :param key_hash: SHA-256 hex digest of the presented key value
        :return: ApiKey record or None if unknown or revoked
        """
        query = select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.revoked_at.is_(None))
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def list_api_keys(self) -> List[ApiKey]:
        """
        List all API key records (including revoked ones), newest first.
        Key values are not recoverable - only name, prefix and timestamps.
        """
        query = select(ApiKey).order_by(ApiKey.created_at.desc())
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def revoke_api_key(self, api_key_id: UUID) -> Optional[ApiKey]:
        """
        Soft-revoke an API key: the record is kept for auditing but the key
        stops authenticating. Idempotent - revoking an already revoked key
        leaves its original revoked_at timestamp.

        :param api_key_id: ID of the key to revoke
        :return: The updated ApiKey record, or None if the ID is unknown
        """
        query = select(ApiKey).where(ApiKey.api_key_id == api_key_id)
        result = await self.db.execute(query)
        api_key = result.scalar_one_or_none()

        if api_key is None:
            logger.warning(f"API key not found for revocation: {api_key_id}")
            return None

        if api_key.revoked_at is None:
            api_key.revoked_at = datetime.now(timezone.utc)
            await self.db.flush()
            logger.info(f"Revoked API key {api_key_id} ({api_key.name})")
        return api_key
