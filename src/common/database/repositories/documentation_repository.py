# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database.models import DocumentationItem
from src.common.database.repositories.relevant_chunk_repository import RelevantChunkRepository

logger = logging.getLogger(__name__)


class DocumentationRepository:
    """Repository for documentation item data access operations."""

    def __init__(self, db: AsyncSession):
        """
        Initialize repository with database session.

        :param db: SQLAlchemy AsyncSession
        """
        self.db = db
        self.relevant_chunk_repo = RelevantChunkRepository(db)

    async def create_documentation_item(
        self,
        session_id: UUID,
        source: str,
        content: str,
        *,
        original_job_id: Optional[UUID] = None,
        doc_id: Optional[UUID] = None,
        url: Optional[str] = None,
        summary: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> UUID:
        """
        Create a new documentation item.

        :param session_id: Associated session ID
        :param source: Source type ('scraper' or 'upload')
        :param content: Documentation content
        :param original_job_id: Optional job ID that created this item (for scraper items)
        :param doc_id: Optional document ID
        :param url: Optional URL
        :param summary: Optional summary
        :param metadata: Optional metadata dict
        :return: Documentation item ID
        """
        doc_item = DocumentationItem(
            session_id=session_id,
            doc_id=doc_id,
            scrape_job_ids=[str(original_job_id)] if original_job_id else [],
            source=source,
            url=url,
            summary=summary,
            content=content,
            doc_metadata=metadata or {},
        )
        self.db.add(doc_item)
        await self.db.flush()
        logger.info(f"Created documentation item {doc_item.chunk_id} for session {session_id}")
        return doc_item.chunk_id

    async def get_documentation_items_by_session(
        self, session_id: UUID, source: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all documentation items for a session.

        :param session_id: Session ID
        :param source: Optional source filter ('scraper' or 'upload')
        :return: List of documentation item dicts
        """
        query = select(DocumentationItem).where(DocumentationItem.session_id == session_id)

        if source:
            query = query.where(DocumentationItem.source == source)

        query = query.order_by(DocumentationItem.created_at)

        result = await self.db.execute(query)
        items = result.scalars().all()

        return [
            {
                "chunkId": str(item.chunk_id),
                "docId": str(item.doc_id) if item.doc_id else None,
                "source": item.source,
                "url": item.url,
                "summary": item.summary,
                "content": item.content,
                "metadata": item.doc_metadata,
            }
            for item in items
        ]

    async def get_documentation_items_by_session_and_job(self, session_id: UUID, job_id: UUID) -> List[Dict[str, Any]]:
        """
        Get documentation items for a session that are related to a specific job.

        :param session_id: Session ID
        :param job_id: Job ID to filter relevant documentation items
        :return: List of documentation item dicts
        """
        query = select(DocumentationItem).where(
            DocumentationItem.session_id == session_id,
            DocumentationItem.scrape_job_ids.is_not(None),
            DocumentationItem.scrape_job_ids.contains([str(job_id)]),
        )

        result = await self.db.execute(query)
        items = result.scalars().all()

        return [
            {
                "chunkId": str(item.chunk_id),
                "docId": str(item.doc_id) if item.doc_id else None,
                "source": item.source,
                "url": item.url,
                "summary": item.summary,
                "content": item.content,
                "metadata": item.doc_metadata,
            }
            for item in items
        ]

    async def update_documentation_item(
        self,
        chunk_id: UUID,
        *,
        source: Optional[str] = None,
        content: Optional[str] = None,
        original_job_id: Optional[UUID] = None,
        doc_id: Optional[UUID] = None,
        url: Optional[str] = None,
        summary: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Update an existing documentation item.

        :param chunk_id: Documentation chunk ID
        :param source: New source (optional)
        :param content: New content (optional)
        :param original_job_id: Optional new job ID that created this item (for scraper items)
        :param doc_id: Optional new document ID
        :param url: Optional new URL
        :param summary: Optional new summary
        :param metadata: Optional new metadata dict
        :return: True if update was successful, False if item not found
        """
        query = select(DocumentationItem).where(DocumentationItem.chunk_id == chunk_id)
        result = await self.db.execute(query)
        item = result.scalar_one_or_none()

        if item is None:
            logger.warning(f"Documentation item not found for update: {chunk_id}")
            return False

        if content is not None:
            item.content = content
        if source is not None:
            item.source = source
        if original_job_id is not None:
            current_ids = item.scrape_job_ids or []
            if str(original_job_id) not in current_ids:
                item.scrape_job_ids = current_ids + [str(original_job_id)]
        if doc_id is not None:
            item.doc_id = doc_id
        if url is not None:
            item.url = url
        if summary is not None:
            item.summary = summary
        if metadata is not None:
            item.doc_metadata = metadata

        await self.db.flush()
        logger.info(f"Updated documentation item {chunk_id}")
        return True

    async def remove_job_ids_from_documentation_items(self, session_id: UUID, doc_source: str) -> int:
        """
        Remove job IDs from documentation items of a specific source for a session.

        :param session_id: Session ID
        :param doc_source: Source type to filter items ('scraper' or 'upload')
        :return: Number of items updated
        """
        query = select(DocumentationItem).where(
            DocumentationItem.session_id == session_id,
            DocumentationItem.source == doc_source,
        )

        result = await self.db.execute(query)
        items = result.scalars().all()

        count = 0
        for item in items:
            item.scrape_job_ids = []
            count += 1

        await self.db.flush()
        logger.info(
            f"Removed job IDs from {count} documentation items for session {session_id} and source {doc_source}"
        )
        return count

    async def remove_documentation_items_by_doc_id(self, session_id: UUID, doc_id: UUID) -> int:
        """
        Remove documentation items for a session that are associated with a specific document ID.

        :param session_id: Session ID
        :param doc_id: Document ID to filter items
        :return: Number of items deleted
        """
        query = select(DocumentationItem).where(
            DocumentationItem.session_id == session_id,
            DocumentationItem.doc_id == doc_id,
        )

        result = await self.db.execute(query)
        items = result.scalars().all()

        count = len(items)
        for item in items:
            await self.db.delete(item)

        await self.db.flush()
        logger.info(f"Deleted {count} documentation items for session {session_id} and document ID {doc_id}")
        return count

    async def get_documentation_item(self, chunk_id: UUID) -> Optional[Dict[str, Any]]:
        """
        Get a single documentation item by ID.

        :param chunk_id: Documentation chunk ID
        :return: Documentation item dict or None
        """
        query = select(DocumentationItem).where(DocumentationItem.chunk_id == chunk_id)
        result = await self.db.execute(query)
        item = result.scalar_one_or_none()

        if item is None:
            return None

        return {
            "chunkId": str(item.chunk_id),
            "sessionId": str(item.session_id),
            "docId": str(item.doc_id) if item.doc_id else None,
            "source": item.source,
            "url": item.url,
            "summary": item.summary,
            "content": item.content,
            "metadata": item.doc_metadata,
        }

    async def delete_documentation_items_by_session(self, session_id: UUID) -> int:
        """
        Delete all documentation items for a session.

        :param session_id: Session ID
        :return: Number of items deleted
        """
        query = select(DocumentationItem).where(DocumentationItem.session_id == session_id)
        result = await self.db.execute(query)
        items = result.scalars().all()

        count = len(items)
        for item in items:
            await self.db.delete(item)

        await self.db.flush()
        logger.info(f"Deleted {count} documentation items for session {session_id}")
        return count

    async def bulk_create_documentation_items(self, session_id: UUID, items: List[Dict[str, Any]]) -> List[UUID]:
        """
        Bulk create documentation items for efficiency.

        :param session_id: Session ID
        :param items: List of item dicts with keys: source, content, doc_id, url, summary, metadata
        :return: List of created documentation item IDs
        """
        ids = []
        for item_data in items:
            doc_item = DocumentationItem(
                session_id=session_id,
                doc_id=item_data.get("doc_id"),
                source=item_data["source"],
                url=item_data.get("url"),
                summary=item_data.get("summary"),
                content=item_data["content"],
                doc_metadata=item_data.get("metadata", {}),
            )
            self.db.add(doc_item)
            ids.append(doc_item.chunk_id)

        await self.db.flush()
        logger.info(f"Bulk created {len(ids)} documentation items for session {session_id}")
        return ids
