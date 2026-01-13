"""
Session management module for FastAPI application.
Provides filesystem-based session storage using JSON files.
"""

#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import config
from ..chunk_processor.llms import get_llm_processed_chunk
from ..chunk_processor.prompts import get_llm_chunk_process_prompt
from ..database.config import async_session_maker
from ..database.repositories.documentation_repository import DocumentationRepository
from ..database.repositories.job_repository import JobRepository
from ..database.repositories.session_repository import SessionRepository
from ..enums import JobStage
from .schema import DocumentationItem, Session

logger = logging.getLogger(__name__)

# TODO: change file management to use database
SESSIONS_DIR = Path(__file__).parent.parent.parent.parent / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


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
        session["data"].update(data)
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


# Helper Functions
async def process_documentation_worker(
    session_id: UUID,
    chunks: List[tuple[str, int]],
    filename: str,
    page_id: UUID,
    app: str,
    app_version: str,
    job_id: UUID,
) -> Dict[str, Any]:
    semaphore = asyncio.Semaphore(config.scrape_and_process.max_concurrent)

    # 1) Initialize progress (and COMMIT so polling can see it immediately)
    async with async_session_maker() as db_init:
        job_repo = JobRepository(db_init)
        await job_repo.update_job_progress(
            job_id,
            stage=JobStage.processing,
            message=f"Processing {len(chunks)} chunks",
            total_processing=len(chunks),
            processing_completed=0,
        )
        await db_init.commit()

    logger.info("[Upload:Job] Processing %s chunks for session %s (job %s)", len(chunks), session_id, job_id)

    async def process_chunk(idx: int, chunk_data: tuple[str, int]) -> DocumentationItem:
        chunk_text, chunk_length = chunk_data

        # 2) LLM part concurrent
        async with semaphore:
            prompts = get_llm_chunk_process_prompt(chunk_text, filename, app, app_version)
            data = await get_llm_processed_chunk(prompts)

        # 3) DB write + progress increment in its OWN session (safe + commits)
        async with async_session_maker() as db_chunk:
            doc_repo = DocumentationRepository(db_chunk)
            job_repo = JobRepository(db_chunk)

            doc_id = await doc_repo.create_documentation_item(
                session_id=session_id,
                source="upload",
                content=chunk_text,
                page_id=page_id,
                url=f"upload://{filename}",
                summary=data.summary,
                metadata={
                    "filename": filename,
                    "chunk_number": idx,
                    "length": chunk_length,
                    "num_endpoints": data.num_endpoints,
                    "tags": data.tags,
                    "category": data.category,
                    "llm_tags": data.tags,
                    "llm_category": data.category,
                },
            )

            await job_repo.increment_processed_documents(job_id, 1)

            # optional: update message as well (purely cosmetic)
            # await job_repo.update_job_progress(job_id, message=f"Processed another chunk")

            await db_chunk.commit()

        return DocumentationItem(
            id=doc_id,
            source="upload",
            page_id=page_id,
            url=f"upload://{filename}",
            summary=data.summary,
            content=chunk_text,
            metadata={
                "filename": filename,
                "chunk_number": idx,
                "length": chunk_length,
                "num_endpoints": data.num_endpoints,
                "tags": data.tags,
                "category": data.category,
                "llm_tags": data.tags,
                "llm_category": data.category,
            },
        )

    doc_items = await asyncio.gather(*[process_chunk(i, ch) for i, ch in enumerate(chunks)])

    # If you still want to store documentationItems in the session JSON,
    # do it once at the end (separate session)
    async with async_session_maker() as db_final:
        session_repo = SessionRepository(db_final)

        existing_docs = await session_repo.get_session_data(session_id, "documentationItems") or []
        for item in doc_items:
            existing_docs.append(item.model_dump(by_alias=True, mode="json"))

        await session_repo.update_session(session_id, {"documentationItems": existing_docs})
        await db_final.commit()

    logger.info(
        "[Upload:Job] Completed processing for session %s (job %s): generated %s chunks",
        session_id,
        job_id,
        len(doc_items),
    )

    return {
        "chunks_processed": len(doc_items),
        "page_id": page_id,
        "filename": filename,
    }


async def get_session_documentation(
    session_id: UUID, documentation: UploadFile | None = None, db: AsyncSession | None = None
) -> list[dict]:
    """
    Helper to get all documentation items from session or uploaded file.
    Can be imported by other module routers.
    Returns list of documentation items with their UUIDs and content.
    """
    if db is None:
        async with async_session_maker() as session:
            return await _get_session_documentation_impl(session_id, documentation, session)
    else:
        return await _get_session_documentation_impl(session_id, documentation, db)


async def _get_session_documentation_impl(
    session_id: UUID, documentation: UploadFile | None, db: AsyncSession
) -> list[dict]:
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    # If an upload is provided, read and store it in the session, then return it
    if documentation is not None:
        doc_text = (await documentation.read()).decode("utf-8", errors="ignore")

        # Create documentation item in database
        doc_repo = DocumentationRepository(db)
        page_id = uuid.uuid4()
        doc_id = await doc_repo.create_documentation_item(
            session_id=session_id,
            source="upload",
            content=doc_text,
            page_id=page_id,
            url=None,
            summary=None,
            metadata={"filename": documentation.filename or "unknown", "length": len(doc_text)},
        )

        # Store as a documentation item with DB id
        existing_docs: list[dict] = await repo.get_session_data(session_id, "documentationItems") or []
        doc_item = DocumentationItem(
            id=doc_id,
            source="upload",
            page_id=page_id,
            url=None,
            summary=None,
            content=doc_text,
            metadata={"filename": documentation.filename or "unknown", "length": len(doc_text)},
        )
        doc_dict = doc_item.model_dump(by_alias=True, mode="json")
        existing_docs.append(doc_dict)
        await repo.update_session(session_id, {"documentationItems": existing_docs})
        await db.commit()

        return [doc_dict]

    # Try to get all documentation items from session
    doc_items = await repo.get_session_data(session_id, "documentationItems")
    if doc_items and len(doc_items) > 0:
        return doc_items

    raise HTTPException(
        status_code=400,
        detail=f"Session {session_id} has no stored documentation. Please upload documentation file or run scraper.",
    )
