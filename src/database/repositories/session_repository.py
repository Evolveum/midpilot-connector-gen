# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Union
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import Session, SessionData
from src.shared.clock import utc_now

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionOwner:
    """Existence + ownership snapshot of a session used for access checks."""

    session_id: UUID
    api_key_id: Optional[UUID]


class SessionRepository:
    """Repository for session data access operations."""

    def __init__(self, db: AsyncSession):
        """
        Initialize repository with database session.

        :param db: SQLAlchemy AsyncSession
        """
        self.db = db

    async def create_session(self, api_key_id: Optional[UUID] = None) -> UUID:
        """
        Create a new session and return its unique ID.

        :param api_key_id: Owning API key, or None for an ownerless session
            (accessible only with the master key when auth is enforced)
        :return: Session ID (UUID)
        """
        session = Session(api_key_id=api_key_id)
        self.db.add(session)
        await self.db.flush()
        logger.info("Created new session: %s", session.session_id)
        return session.session_id

    async def create_session_with_id(self, session_id: UUID, api_key_id: Optional[UUID] = None) -> UUID:
        """
        Create a new session with a provided ID.
        If the session already exists, raises ValueError.

        :param session_id: The UUID to use for the session
        :param api_key_id: Owning API key, or None for an ownerless session
        :return: Session ID
        """
        session = Session(session_id=session_id, api_key_id=api_key_id)
        self.db.add(session)
        await self.db.flush()
        logger.info("Created new session with provided ID: %s", session_id)
        return session_id

    async def get_session(self, session_id: UUID) -> Optional[Dict[str, Any]]:
        """
        Retrieve session data by session ID.

        :param session_id: The session ID to retrieve
        :return: Session data dict or None if not found
        """
        query = select(Session).where(Session.session_id == session_id)
        result = await self.db.execute(query)
        session = result.scalar_one_or_none()

        if session is None:
            logger.warning("Session not found: %s", session_id)
            return None

        # Get all session_data for this session
        data = select(SessionData).where(SessionData.session_id == session_id)
        data_result = await self.db.execute(data)
        session_data_records = data_result.scalars().all()

        # Build data dict from session_data records
        data_dict: Dict[str, Any] = {}
        for record in session_data_records:
            data_dict[record.key] = record.value

        return {
            "sessionId": str(session.session_id),
            "createdAt": session.created_at.isoformat(),
            "updatedAt": session.updated_at.isoformat(),
            "data": data_dict,
        }

    async def update_session(self, session_id: UUID, data: Dict[str, Any]) -> bool:
        """
        Update session data. Replaces existing keys with new values or adds new keys.

        :param session_id: The session ID to update
        :param data: Dictionary of data to store/update in the session
        :return: True if successful, False otherwise
        """
        # Check if session exists
        query = select(Session).where(Session.session_id == session_id).with_for_update()
        result = await self.db.execute(query)
        session = result.scalar_one_or_none()

        if session is None:
            logger.error("Cannot update non-existent session: %s", session_id)
            return False

        # Update session timestamp
        session.updated_at = utc_now()

        # Atomic PostgreSQL upserts avoid unique-key races when multiple jobs
        # update different or identical session fields concurrently.
        for key, value in data.items():
            await self._upsert_session_data(session_id, key, value)

        await self.db.flush()
        logger.info("Updated session: %s", session_id)
        return True

    async def lock_session(self, session_id: UUID) -> bool:
        """Serialize a read-modify-write sequence for one session."""
        query = select(Session.session_id).where(Session.session_id == session_id).with_for_update()
        return (await self.db.execute(query)).scalar_one_or_none() is not None

    async def update_locked_session(self, session_id: UUID, data: Dict[str, Any]) -> bool:
        """Update data after the caller has already locked the session row."""
        now = utc_now()
        result = await self.db.execute(update(Session).where(Session.session_id == session_id).values(updated_at=now))
        if not bool(getattr(result, "rowcount", 0)):
            return False
        for key, value in data.items():
            await self._upsert_session_data(session_id, key, value)
        await self.db.flush()
        return True

    async def _upsert_session_data(self, session_id: UUID, key: str, value: Any) -> None:
        now = utc_now()
        statement = (
            insert(SessionData)
            .values(
                session_id=session_id,
                key=key,
                value=value,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[SessionData.session_id, SessionData.key],
                set_={
                    "value": value,
                    "updated_at": now,
                },
            )
        )
        await self.db.execute(statement)

    async def update_result_if_current_job(
        self,
        *,
        session_id: UUID,
        result_key: str,
        job_id: UUID,
        value: Any,
    ) -> bool:
        """Write a result only while the session still points at this job.

        Locking the job-pointer row serializes a result write with a concurrent
        request scheduling a newer job for the same output key.
        """
        if not result_key.endswith("Output"):
            raise ValueError(f"Session result key {result_key!r} does not follow the *Output convention")
        pointer_key = f"{result_key[: -len('Output')]}JobId"
        session = (
            await self.db.execute(select(Session).where(Session.session_id == session_id).with_for_update())
        ).scalar_one_or_none()
        if session is None:
            return False
        if not await self.is_current_job_pointer(
            session_id=session_id,
            pointer_key=pointer_key,
            job_id=job_id,
            lock=True,
        ):
            return False

        await self._upsert_session_data(session_id, result_key, value)
        session.updated_at = utc_now()
        await self.db.flush()
        return True

    async def is_current_job_pointer(
        self,
        *,
        session_id: UUID,
        pointer_key: str,
        job_id: UUID,
        lock: bool = False,
    ) -> bool:
        """Check an explicit session job pointer, optionally locking its row."""
        pointer_query = select(SessionData).where(
            SessionData.session_id == session_id,
            SessionData.key == pointer_key,
        )
        if lock:
            pointer_query = pointer_query.with_for_update()
        pointer = (await self.db.execute(pointer_query)).scalar_one_or_none()
        if pointer is None or str(pointer.value) != str(job_id):
            logger.warning(
                "Skipped stale write from job %s for session %s pointer %s; current pointer is %s",
                job_id,
                session_id,
                pointer_key,
                pointer.value if pointer else None,
            )
            return False
        return True

    async def get_session_value(self, session_id: UUID, key: str) -> Optional[Any]:
        """Read one session-data value without loading every key in the session."""
        return (
            await self.db.execute(
                select(SessionData.value).where(
                    SessionData.session_id == session_id,
                    SessionData.key == key,
                )
            )
        ).scalar_one_or_none()

    async def get_session_values(self, session_id: UUID, keys: Sequence[str]) -> Dict[str, Any]:
        """
        Read several session-data rows in one query.

        Sits between ``get_session_value`` (one row) and the keyless
        ``get_session_data`` (every stored payload): callers that need a known set
        of keys - e.g. every generated Groovy script of a connector - would
        otherwise pay one round trip per key or materialize the whole session.

        :param session_id: The session ID
        :param keys: The keys to read; duplicates are collapsed
        :return: Mapping of key to value, omitting keys that do not exist
        """
        unique_keys = list(dict.fromkeys(keys))
        if not unique_keys:
            return {}

        rows = await self.db.execute(
            select(SessionData.key, SessionData.value).where(
                SessionData.session_id == session_id,
                SessionData.key.in_(unique_keys),
            )
        )
        return {key: value for key, value in rows.all()}

    async def get_session_data(self, session_id: UUID, key: Optional[Union[str, List[str]]] = None) -> Optional[Any]:
        """
        Get data from a session.

        Only a keyless call needs the whole session. A single key, or the root of
        a nested path, is read as one row so callers do not materialize every
        stored ``*Output`` payload just to reach one of them.

        :param session_id: The session ID
        :param key: Optional key to retrieve specific data, can be str or list of str for nested keys
        :return: The requested data or None if not found
        """
        if key is None:
            session = await self.get_session(session_id)
            return None if session is None else session.get("data", {})

        if isinstance(key, str):
            return await self.get_session_value(session_id, key)

        if not key:
            return None

        # Navigate nested keys, loading only the root key of the path
        value = await self.get_session_value(session_id, key[0])
        for step in key[1:]:
            if not isinstance(value, dict):
                logger.warning(
                    "Expected dict while traversing session data for session %s, got %s",
                    session_id,
                    type(value),
                )
                return None
            value = value.get(step)
        return value

    async def delete_session(self, session_id: UUID) -> bool:
        """
        Delete a session.

        :param session_id: The session ID to delete
        :return: True if successful, False otherwise
        """
        query = select(Session).where(Session.session_id == session_id)
        result = await self.db.execute(query)
        session = result.scalar_one_or_none()

        if session is None:
            logger.warning("Session not found for deletion: %s", session_id)
            return False

        await self.db.delete(session)
        await self.db.flush()
        logger.info("Deleted session: %s", session_id)
        return True

    async def get_session_owner(self, session_id: UUID) -> Optional[SessionOwner]:
        """
        Fetch existence and ownership of a session in a single query.

        :param session_id: The session ID to look up
        :return: SessionOwner (api_key_id is None for ownerless sessions),
            or None if the session does not exist
        """
        query = select(Session.session_id, Session.api_key_id).where(Session.session_id == session_id)
        result = await self.db.execute(query)
        row = result.one_or_none()
        if row is None:
            return None
        return SessionOwner(session_id=row.session_id, api_key_id=row.api_key_id)

    async def session_exists(self, session_id: UUID) -> bool:
        """
        Check if a session exists.

        :param session_id: The session ID to check
        :return: True if session exists, False otherwise
        """
        query = select(Session.session_id).where(Session.session_id == session_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none() is not None
