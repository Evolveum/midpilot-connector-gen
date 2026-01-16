#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Session, SessionData

logger = logging.getLogger(__name__)


class SessionRepository:
    """Repository for session data access operations."""

    def __init__(self, db: AsyncSession):
        """
        Initialize repository with database session.

        :param db: SQLAlchemy AsyncSession
        """
        self.db = db

    @staticmethod
    def now_iso() -> str:
        """Return current UTC timestamp as ISO formatted string."""
        return datetime.now(timezone.utc).isoformat()

    async def create_session(self) -> UUID:
        """
        Create a new session and return its unique ID.

        :return: Session ID (UUID)
        """
        session = Session()
        self.db.add(session)
        await self.db.flush()
        logger.info(f"Created new session: {session.session_id}")
        return session.session_id

    async def create_session_with_id(self, session_id: UUID) -> UUID:
        """
        Create a new session with a provided ID.
        If the session already exists, raises ValueError.

        :param session_id: The UUID to use for the session
        :return: Session ID
        """
        session = Session(session_id=session_id)
        self.db.add(session)
        await self.db.flush()
        logger.info(f"Created new session with provided ID: {session_id}")
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
            logger.warning(f"Session not found: {session_id}")
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
        Update session data. Merges new data with existing data.

        :param session_id: The session ID to update
        :param data: Dictionary of data to store/update in the session
        :return: True if successful, False otherwise
        """
        # Check if session exists
        query = select(Session).where(Session.session_id == session_id)
        result = await self.db.execute(query)
        session = result.scalar_one_or_none()

        if session is None:
            logger.error(f"Cannot update non-existent session: {session_id}")
            return False

        # Update session timestamp
        session.updated_at = datetime.now(timezone.utc)

        # Update or insert session_data records
        for key, value in data.items():
            # Check if key exists
            query = select(SessionData).where(SessionData.session_id == session_id, SessionData.key == key)
            data_result = await self.db.execute(query)
            session_data = data_result.scalar_one_or_none()

            if session_data:
                # Update existing
                session_data.value = value
                session_data.updated_at = datetime.now(timezone.utc)
            else:
                # Create new
                session_data = SessionData(session_id=session_id, key=key, value=value)
                self.db.add(session_data)

        await self.db.flush()
        logger.info(f"Updated session: {session_id}")
        return True

    async def get_session_data(self, session_id: UUID, key: Optional[Union[str, List[str]]] = None) -> Optional[Any]:
        """
        Get data from a session.

        :param session_id: The session ID
        :param key: Optional key to retrieve specific data, can be str or list of str for nested keys
        :return: The requested data or None if not found
        """
        session = await self.get_session(session_id)
        if session is None:
            return None

        data = session.get("data", {})
        if key is None:
            return data

        if isinstance(key, list):
            # Navigate nested keys
            idx = 0
            while idx < len(key) - 1:
                data = data.get(key[idx])
                if not isinstance(data, dict):
                    logger.warning(
                        f"Expected dict while traversing session data for session {session_id}, got {type(data)}"
                    )
                    return None
                idx += 1
            return data.get(key[-1])
        else:
            return data.get(key)

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
            logger.warning(f"Session not found for deletion: {session_id}")
            return False

        await self.db.delete(session)
        await self.db.flush()
        logger.info(f"Deleted session: {session_id}")
        return True

    async def session_exists(self, session_id: UUID) -> bool:
        """
        Check if a session exists.

        :param session_id: The session ID to check
        :return: True if session exists, False otherwise
        """
        query = select(Session.session_id).where(Session.session_id == session_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none() is not None
