# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from src.core.errors import ExecutionOwnershipLostError
from src.core.job_execution import get_current_execution
from src.database.models import DocumentationItem
from src.database.repositories.job_repository import JobRepository
from src.shared.content_types import CONNDEV_CONTENT_TYPES

logger = logging.getLogger(__name__)


class DocumentationWriteBatch:
    """Commit documentation writes in bounded transactions."""

    def __init__(self, db: AsyncSession, batch_size: int):
        self.db = db
        self.batch_size = batch_size
        self.pending_writes = 0

    async def record_write(self) -> None:
        self.pending_writes += 1
        if self.pending_writes >= self.batch_size:
            await self.commit_pending()

    async def commit_pending(self) -> None:
        if self.pending_writes == 0:
            return
        await self.db.commit()
        self.pending_writes = 0


class DocumentationRepository:
    """Repository for documentation item data access operations."""

    def __init__(self, db: AsyncSession):
        """
        Initialize repository with database session.

        :param db: SQLAlchemy AsyncSession
        """
        self.db = db
        self._fenced_transaction: AsyncSessionTransaction | None = None
        self._fenced_execution: tuple[UUID, str, UUID] | None = None

    async def _assert_current_execution(self, job_id: UUID) -> None:
        execution = get_current_execution()
        if execution is None:
            return
        if execution.job_id != job_id:
            raise ExecutionOwnershipLostError(job_id)

        current_transaction = self.db.get_transaction()
        execution_identity = (job_id, execution.worker_id, execution.execution_token)
        if current_transaction is self._fenced_transaction and execution_identity == self._fenced_execution:
            return

        await JobRepository(self.db).acquire_execution_fence(
            job_id,
            worker_id=execution.worker_id,
            execution_token=execution.execution_token,
        )
        self._fenced_transaction = self.db.get_transaction()
        self._fenced_execution = execution_identity

    @staticmethod
    def _build_origin_key(
        *,
        source: str,
        content: str,
        url: Optional[str],
        metadata: Dict[str, Any],
    ) -> str:
        identity = {
            "source": source,
            "url": url,
            "chunk_number": metadata.get("chunk_number"),
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
        return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    @staticmethod
    def _to_item_dict(item: DocumentationItem) -> Dict[str, Any]:
        """Map a documentation row to the dict shape shared by the read queries."""
        return {
            "chunkId": str(item.chunk_id),
            "docId": str(item.doc_id) if item.doc_id else None,
            "source": item.source,
            "url": item.url,
            "summary": item.summary,
            "content": item.content,
            "metadata": item.doc_metadata,
        }

    @classmethod
    def _to_export_item_dict(cls, item: DocumentationItem) -> Dict[str, Any]:
        """Map a documentation row to the complete export/API response shape."""
        return {
            **cls._to_item_dict(item),
            "createdAt": item.created_at.isoformat(),
            "scrapeJobIds": list(item.scrape_job_ids or []),
        }

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
        if original_job_id is not None:
            await self._assert_current_execution(original_job_id)
            doc_metadata = metadata or {}
            origin_key = self._build_origin_key(
                source=source,
                content=content,
                url=url,
                metadata=doc_metadata,
            )
            statement = (
                insert(DocumentationItem)
                .values(
                    session_id=session_id,
                    doc_id=doc_id,
                    scrape_job_ids=[str(original_job_id)],
                    origin_job_id=original_job_id,
                    origin_key=origin_key,
                    source=source,
                    url=url,
                    summary=summary,
                    content=content,
                    doc_metadata=doc_metadata,
                )
                .on_conflict_do_update(
                    constraint="uq_doc_items_job_origin",
                    set_={
                        "doc_id": doc_id,
                        "scrape_job_ids": case(
                            (
                                DocumentationItem.scrape_job_ids.contains([str(original_job_id)]),
                                DocumentationItem.scrape_job_ids,
                            ),
                            else_=DocumentationItem.scrape_job_ids.concat([str(original_job_id)]),
                        ),
                        "source": source,
                        "url": url,
                        "summary": summary,
                        "content": content,
                        "metadata": doc_metadata,
                    },
                )
                .returning(DocumentationItem.chunk_id)
            )
            chunk_id = (await self.db.execute(statement)).scalar_one()
            await self.db.flush()
            logger.info("Upserted chunk_id %s for session %s and job %s", chunk_id, session_id, original_job_id)
            return chunk_id

        doc_item = DocumentationItem(
            session_id=session_id,
            doc_id=doc_id,
            scrape_job_ids=[],
            source=source,
            url=url,
            summary=summary,
            content=content,
            doc_metadata=metadata or {},
        )
        self.db.add(doc_item)
        await self.db.flush()
        logger.info(f"Created chunk_id {doc_item.chunk_id} for session {session_id}")
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

        return [self._to_item_dict(item) for item in items]

    async def get_conndev_documentation_items_by_session(self, session_id: UUID) -> List[Dict[str, Any]]:
        """
        Get only the session's midPoint connector-development (conndev) documents.

        Filters by the metadata content type in the database so the session's full
        documentation is never loaded when only the few conndev contract documents are
        needed. The SQL normalization mirrors ``normalize_content_type``: parameters after
        ``;`` are dropped and the value is trimmed and lower-cased before comparison.

        :param session_id: Session ID
        :return: List of documentation item dicts, oldest first
        """
        normalized_content_type = func.lower(
            func.btrim(func.split_part(DocumentationItem.doc_metadata["content_type"].astext, ";", 1))
        )
        query = (
            select(DocumentationItem)
            .where(
                DocumentationItem.session_id == session_id,
                normalized_content_type.in_(sorted(CONNDEV_CONTENT_TYPES)),
            )
            .order_by(DocumentationItem.created_at)
        )

        result = await self.db.execute(query)
        items = result.scalars().all()

        return [self._to_item_dict(item) for item in items]

    async def get_documentation_items_by_doc_id(self, session_id: UUID, doc_id: UUID) -> List[Dict[str, Any]]:
        """
        Get all documentation items for one logical document (doc_id) in a session.

        :param session_id: Session ID
        :param doc_id: Document ID
        :return: List of documentation item dicts
        """
        query = (
            select(DocumentationItem)
            .where(
                DocumentationItem.session_id == session_id,
                DocumentationItem.doc_id == doc_id,
            )
            .order_by(DocumentationItem.created_at, DocumentationItem.chunk_id)
        )

        items = (await self.db.execute(query)).scalars().all()
        return [self._to_export_item_dict(item) for item in items]

    async def get_documentation_items_for_export(self, session_id: UUID) -> List[Dict[str, Any]]:
        """
        Get documentation items for export for a specific session.

        :param session_id: Session ID
        :return: List of documentation item dicts
        """
        query = select(DocumentationItem).where(DocumentationItem.session_id == session_id)

        query = query.order_by(
            DocumentationItem.doc_id,
            DocumentationItem.created_at,
            DocumentationItem.chunk_id,
        )

        result = await self.db.execute(query)
        items = result.scalars().all()

        return [self._to_export_item_dict(item) for item in items]

    async def get_scraped_documentation_items_for_export_by_job(
        self,
        session_id: UUID,
        job_id: UUID,
    ) -> List[Dict[str, Any]]:
        """Get every scraper chunk one job is responsible for, for that job's own result payload."""
        query = (
            select(DocumentationItem)
            .where(
                DocumentationItem.session_id == session_id,
                or_(
                    DocumentationItem.origin_job_id == job_id,
                    DocumentationItem.scrape_job_ids.contains([str(job_id)]),
                ),
                DocumentationItem.source == "scraper",
            )
            .order_by(
                DocumentationItem.doc_id,
                DocumentationItem.created_at,
                DocumentationItem.chunk_id,
            )
        )

        items = (await self.db.execute(query)).scalars().all()
        return [self._to_export_item_dict(item) for item in items]

    @staticmethod
    def _parse_iso_datetime(value: str) -> datetime:
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    async def import_documentation_items_for_session(self, session_id: UUID, items: List[Dict[str, Any]]) -> int:
        """
        Import documentation items for a specific session preserving exported fields.

        :param session_id: Session ID
        :param items: Flat list of chunk dictionaries
        :return: Number of imported chunks
        """
        imported_count = 0
        for item in items:
            created_at_raw = item.get("createdAt")
            created_at = self._parse_iso_datetime(created_at_raw) if isinstance(created_at_raw, str) else None

            doc_kwargs = {
                "chunk_id": UUID(str(item["chunkId"])),
                "session_id": session_id,
                "doc_id": UUID(str(item["docId"])) if item.get("docId") else None,
                "scrape_job_ids": [str(job_id) for job_id in (item.get("scrapeJobIds") or [])],
                "source": str(item["source"]),
                "url": item.get("url"),
                "summary": item.get("summary"),
                "content": str(item["content"]),
                "doc_metadata": item.get("metadata") or {},
            }
            if created_at is not None:
                doc_kwargs["created_at"] = created_at

            doc_item = DocumentationItem(
                **doc_kwargs,
            )
            self.db.add(doc_item)
            imported_count += 1

        await self.db.flush()
        logger.info(f"Imported {imported_count} documentation items for session {session_id}")
        return imported_count

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

        return [self._to_item_dict(item) for item in items]

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
        if original_job_id is not None:
            await self._assert_current_execution(original_job_id)
        query = select(DocumentationItem).where(DocumentationItem.chunk_id == chunk_id).with_for_update()
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
        logger.info(f"Updated documentation item with chunk_id: {chunk_id}")
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

        return {**self._to_item_dict(item), "sessionId": str(item.session_id)}

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
