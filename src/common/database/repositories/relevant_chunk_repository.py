# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import RelevantChunk

logger = logging.getLogger(__name__)


class RelevantChunkRepository:
    """Repository for relevant chunk data access operations."""

    def __init__(self, db: AsyncSession):
        """
        Initialize repository with database session.

        :param db: SQLAlchemy AsyncSession
        """
        self.db = db

    async def add_relevant_chunk(self, session_id: UUID, entity_type: str, doc_id: UUID) -> bool:
        """
        Add a single relevant chunk, checking for duplicates.

        :param session_id: Session ID
        :param entity_type: Entity type (e.g., 'User', 'Group', 'Project')
        :param doc_id: Documentation item ID
        :return: True if inserted, False if already exists
        """
        # Check if already exists
        stmt = select(RelevantChunk).where(
            RelevantChunk.session_id == session_id,
            RelevantChunk.entity_type == entity_type,
            RelevantChunk.doc_id == doc_id,
        )
        result = await self.db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing is not None:
            return False

        # Insert new chunk
        chunk = RelevantChunk(
            session_id=session_id,
            entity_type=entity_type,
            doc_id=doc_id,
        )
        self.db.add(chunk)
        await self.db.flush()
        logger.debug(f"Added relevant chunk: session={session_id}, entity={entity_type}, doc={doc_id}")
        return True

    async def bulk_add_relevant_chunks(self, session_id: UUID, chunks: List[Dict[str, Any]]) -> int:
        """
        Bulk add relevant chunks with duplicate checking.

        :param session_id: Session ID
        :param chunks: List of dicts with 'entity_type' and 'doc_id' keys
        :return: Number of chunks inserted
        """
        if not chunks:
            return 0

        inserted = 0
        for chunk_info in chunks:
            try:
                entity_type = chunk_info.get("entity_type")
                doc_id = chunk_info.get("doc_id")

                if not entity_type or not doc_id:
                    continue

                # Convert string UUID to UUID object if needed
                if isinstance(doc_id, str):
                    doc_id = UUID(doc_id)

                # Check for duplicates
                stmt = select(RelevantChunk).where(
                    RelevantChunk.session_id == session_id,
                    RelevantChunk.entity_type == entity_type,
                    RelevantChunk.doc_id == doc_id,
                )
                result = await self.db.execute(stmt)
                if result.scalar_one_or_none() is not None:
                    continue  # Skip duplicates

                # Insert new chunk
                chunk = RelevantChunk(
                    session_id=session_id,
                    entity_type=entity_type,
                    doc_id=doc_id,
                )
                self.db.add(chunk)
                inserted += 1

            except Exception as e:
                logger.debug(f"Failed to insert chunk {chunk_info}: {e}")
                continue

        if inserted > 0:
            await self.db.flush()
            logger.info(f"Bulk added {inserted} relevant chunks for session {session_id}")

        return inserted

    async def get_relevant_chunks(self, session_id: UUID, entity_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get relevant chunks for a session, optionally filtered by entity type.

        :param session_id: Session ID
        :param entity_type: Optional entity type filter (e.g., 'User', 'Group')
        :return: List of chunk dicts with docId and entityType
        """
        stmt = select(RelevantChunk).where(RelevantChunk.session_id == session_id)

        if entity_type:
            stmt = stmt.where(RelevantChunk.entity_type == entity_type)

        stmt = stmt.order_by(RelevantChunk.created_at)

        result = await self.db.execute(stmt)
        chunks = result.scalars().all()

        return [
            {
                "docId": str(chunk.doc_id),
                "entityType": chunk.entity_type,
            }
            for chunk in chunks
        ]

    async def get_relevant_chunks_for_entity(self, session_id: UUID, entity_type: str) -> List[Dict[str, Any]]:
        """
        Get relevant chunks for a specific entity type.

        :param session_id: Session ID
        :param entity_type: Entity type (e.g., 'User', 'Group', 'Project')
        :return: List of chunk dicts with docId
        """
        chunks = await self.get_relevant_chunks(session_id, entity_type)
        return [{"docId": chunk["docId"]} for chunk in chunks]

    async def delete_by_session(self, session_id: UUID) -> int:
        """
        Delete all relevant chunks for a session.

        :param session_id: Session ID
        :return: Number of chunks deleted
        """
        stmt = select(RelevantChunk).where(RelevantChunk.session_id == session_id)
        result = await self.db.execute(stmt)
        chunks = result.scalars().all()

        count = len(chunks)
        for chunk in chunks:
            await self.db.delete(chunk)

        if count > 0:
            await self.db.flush()
            logger.info(f"Deleted {count} relevant chunks for session {session_id}")

        return count

    async def count_by_session(self, session_id: UUID) -> int:
        """
        Count relevant chunks for a session.

        :param session_id: Session ID
        :return: Number of chunks
        """
        stmt = select(RelevantChunk).where(RelevantChunk.session_id == session_id)
        result = await self.db.execute(stmt)
        chunks = result.scalars().all()
        return len(chunks)

    async def count_by_entity(self, session_id: UUID, entity_type: str) -> int:
        """
        Count relevant chunks for a specific entity type.

        :param session_id: Session ID
        :param entity_type: Entity type
        :return: Number of chunks
        """
        stmt = select(RelevantChunk).where(
            RelevantChunk.session_id == session_id,
            RelevantChunk.entity_type == entity_type,
        )
        result = await self.db.execute(stmt)
        chunks = result.scalars().all()
        return len(chunks)
