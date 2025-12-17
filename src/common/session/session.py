# Copyright (c) 2025 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Session management module for FastAPI application.
Provides filesystem-based session storage using JSON files.
"""

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import UUID

from .schema import Session

logger = logging.getLogger(__name__)

# TODO: change file management to use database
SESSIONS_DIR = Path(__file__).parent.parent.parent.parent / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _serialize_for_json(obj: Any) -> Any:
    """Recursively convert non-JSON-serializable objects (like UUID) to JSON-compatible types."""
    if isinstance(obj, UUID):
        return str(obj)
    elif isinstance(obj, dict):
        return {key: _serialize_for_json(value) for key, value in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_serialize_for_json(item) for item in obj]
    elif hasattr(obj, "model_dump"):
        # Handle Pydantic models
        return obj.model_dump(by_alias=True, mode="json")
    else:
        return obj


class SessionManager:
    """Manages session data stored as JSON files on the filesystem."""

    @staticmethod
    def now_iso() -> str:
        """Return current UTC timestamp as ISO formatted string."""
        return datetime.now(UTC).isoformat()

    @staticmethod
    def write_session_file(path: Path, data: Dict[str, Any]) -> bool:
        """Write a JSON file to the given path. Returns True on success."""
        try:
            with path.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except IOError as e:
            logger.error(f"Failed to write session file {path}: {e}")
            return False

    @staticmethod
    def read_session_file(path: Path) -> Optional[Dict[str, Any]]:
        """Read and return JSON data from the given path, or None on error."""
        try:
            with path.open(encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to read session file {path}: {e}")
            return None

    @staticmethod
    def get_session_path(session_id: UUID) -> Path:
        """Get the file path for a session ID."""
        return SESSIONS_DIR / f"{str(session_id)}.json"

    @staticmethod
    def create_session() -> UUID:
        """
        Create a new session and return its unique ID.

        :return: Session ID (UUID v4)
        """
        session_id: UUID = uuid.uuid4()
        session_model = Session(
            sessionId=session_id,
            createdAt=SessionManager.now_iso(),
            updatedAt=SessionManager.now_iso(),
            data={},
        )

        session_path = SessionManager.get_session_path(session_id)
        ok = SessionManager.write_session_file(session_path, session_model.model_dump(mode="json"))
        if not ok:
            raise RuntimeError("Unable to persist new session")

        logger.info("Created new session: %s", session_id)
        return session_id

    @staticmethod
    def create_session_with_id(session_id: UUID) -> UUID:
        """
        Create a new session with a provided ID.
        If the session already exists, raises a FileExistsError.
        """
        session_path = SessionManager.get_session_path(session_id)
        if session_path.exists():
            raise FileExistsError(f"Session {session_id} already exists")

        session_model = Session(
            sessionId=session_id,
            createdAt=SessionManager.now_iso(),
            updatedAt=SessionManager.now_iso(),
            data={},
        )
        SessionManager.write_session_file(session_path, session_model.model_dump(mode="json"))

        logger.info(f"Created new session with provided ID: {session_id}")
        return session_id

    @staticmethod
    def get_session(session_id: UUID) -> Optional[Dict[str, Any]]:
        """
        Retrieve session data by session ID.

        :param session_id: The session ID to retrieve
        :return: Session data dict or None if not found
        """
        session_path = SessionManager.get_session_path(session_id)
        if not session_path.exists():
            logger.warning(f"Session not found: {session_id}")
            return None

        return SessionManager.read_session_file(session_path)

    @staticmethod
    def update_session(session_id: UUID, data: Dict[str, Any]) -> bool:
        """
        Update session data. Merges new data with existing data.

        :param session_id: The session ID to update
        :param data: Dictionary of data to store/update in the session
        :return: True if successful, False otherwise
        """
        session = SessionManager.get_session(session_id)
        if session is None:
            logger.error(f"Cannot update non-existent session: {session_id}")
            return False

        # Merge new data into existing session data
        session.setdefault("data", {})
        session["data"].update(_serialize_for_json(data))
        session["updatedAt"] = SessionManager.now_iso()

        session_path = SessionManager.get_session_path(session_id)
        success = SessionManager.write_session_file(session_path, session)
        if success:
            logger.info(f"Updated session: {session_id}")
        else:
            logger.error(f"Failed to update session: {session_id}")
        return success

    @staticmethod
    def get_session_data(session_id: UUID, key: Optional[str | list[str]] = None) -> Optional[Any]:
        """
        Get data from a session.

        :param session_id: The session ID
        :param key: Optional key to retrieve specific data, it can be a str for one level key or a list of str for nested keys
        :return: The requested data or None if not found
        """
        session = SessionManager.get_session(session_id)
        if session is None:
            return None

        data = session.get("data", {})
        if key is None:
            return data
        if isinstance(key, list):
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

    @staticmethod
    def delete_session(session_id: UUID) -> bool:
        """
        Delete a session.

        :param session_id: The session ID to delete
        :return: True if successful, False otherwise
        """
        session_path = SessionManager.get_session_path(session_id)
        if not session_path.exists():
            logger.warning(f"Session not found for deletion: {session_id}")
            return False

        try:
            session_path.unlink()
            logger.info(f"Deleted session: {session_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete session {session_id} at {session_path}: {e}")
            return False

    @staticmethod
    def session_exists(session_id: UUID) -> bool:
        """
        Check if a session exists.

        :param session_id: The session ID to check
        :return: True if session exists, False otherwise
        """
        return SessionManager.get_session_path(session_id).exists()
