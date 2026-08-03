# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

from sqlalchemy import Select, case, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction
from sqlalchemy.orm import contains_eager

from src.core.errors import ExecutionOwnershipLostError
from src.core.job_execution import get_current_execution
from src.database.models import Document, DocumentationChunk
from src.database.repositories.job_repository import JobRepository
from src.shared.content_types import CONNDEV_CONTENT_TYPES

logger = logging.getLogger(__name__)

_DOCUMENT_METADATA_KEYS = ("content_type", "filename")
_CHUNK_NUMBER_KEY = "chunk_number"


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
    """Repository for documentation data access operations.

    Documentation is stored as a document (``documents``) with its chunks
    (``documentation_chunks``); the dicts returned here flatten the two back into
    the one shape the API and the pipelines consume.
    """

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
        chunk_number: Optional[int],
    ) -> str:
        identity = {
            "source": source,
            "url": url,
            "chunk_number": chunk_number,
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
        return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    @staticmethod
    def _coerce_chunk_number(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            logger.warning("Ignoring non-numeric chunk_number %r", value)
            return None

    @classmethod
    def _split_metadata(
        cls, metadata: Optional[Dict[str, Any]]
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Optional[int]]:
        """Split an incoming metadata dict into its chunk, document and position parts."""
        source_metadata = dict(metadata or {})
        document_fields = {key: source_metadata.pop(key, None) for key in _DOCUMENT_METADATA_KEYS}
        chunk_number = cls._coerce_chunk_number(source_metadata.pop(_CHUNK_NUMBER_KEY, None))
        return source_metadata, document_fields, chunk_number

    @staticmethod
    def _merged_metadata(chunk: DocumentationChunk) -> Dict[str, Any]:
        """Rebuild the metadata dict callers expect from the split storage.

        Key set and values are exactly what a single flat row returned. Their
        order is not part of the contract and never was: ``metadata`` is a JSONB
        column, and PostgreSQL stores its keys in its own order rather than the
        one they were written in.
        """
        merged: Dict[str, Any] = {}
        if chunk.chunk_number is not None:
            merged[_CHUNK_NUMBER_KEY] = chunk.chunk_number
        merged.update(chunk.doc_metadata or {})
        document = chunk.document
        for key in _DOCUMENT_METADATA_KEYS:
            value = getattr(document, key, None)
            if value is not None:
                merged[key] = value
        return merged

    @classmethod
    def _to_item_dict(cls, chunk: DocumentationChunk) -> Dict[str, Any]:
        """Map one chunk and its document to the dict shape shared by the read queries."""
        return {
            "chunkId": str(chunk.chunk_id),
            "docId": str(chunk.doc_id),
            "source": chunk.document.source,
            "url": chunk.document.url,
            "summary": chunk.summary,
            "content": chunk.content,
            "metadata": cls._merged_metadata(chunk),
        }

    @classmethod
    def _to_export_item_dict(cls, chunk: DocumentationChunk) -> Dict[str, Any]:
        """Map one chunk to the complete export/API response shape."""
        return {
            **cls._to_item_dict(chunk),
            "createdAt": chunk.created_at.isoformat(),
            "scrapeJobIds": list(chunk.scrape_job_ids or []),
        }

    @staticmethod
    def _chunks_with_document() -> Select[Tuple[DocumentationChunk]]:
        """Select chunks joined to their document.

        The join is explicit so callers can filter on document-level columns, and
        ``contains_eager`` reuses it to populate the relationship - serialization
        then needs no second query. An inner join is correct because the chunk's
        document reference is NOT NULL and enforced by a foreign key.
        """
        return (
            select(DocumentationChunk)
            .join(DocumentationChunk.document)
            .options(contains_eager(DocumentationChunk.document))
        )

    async def _upsert_document(
        self,
        *,
        session_id: UUID,
        doc_id: UUID,
        source: str,
        url: Optional[str],
        document_fields: Dict[str, Any],
    ) -> None:
        """Create or refresh the document a chunk belongs to.

        A chunk that carries no filename or content type must not erase what the
        document already knows, so those fields only ever move from NULL to a
        value.
        """
        values = {
            "session_id": session_id,
            "doc_id": doc_id,
            "source": source,
            "url": url,
            **{key: document_fields.get(key) for key in _DOCUMENT_METADATA_KEYS},
        }
        statement = insert(Document).values(**values)
        await self.db.execute(
            statement.on_conflict_do_update(
                index_elements=[Document.session_id, Document.doc_id],
                set_={
                    "source": source,
                    "url": url,
                    **{
                        key: func.coalesce(getattr(statement.excluded, key), getattr(Document, key))
                        for key in _DOCUMENT_METADATA_KEYS
                    },
                },
            )
        )

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
        Create a documentation chunk, creating or refreshing its document first.

        :param session_id: Associated session ID
        :param source: Source type ('scraper' or 'upload')
        :param content: Documentation content
        :param original_job_id: Optional job ID that created this item (for scraper items)
        :param doc_id: Document this chunk belongs to. A new single-chunk document
            is created when omitted; callers writing several chunks of one
            document must pass the same id for all of them.
        :param url: Optional URL
        :param summary: Optional summary
        :param metadata: Optional metadata dict
        :return: Documentation chunk ID
        """
        chunk_metadata, document_fields, chunk_number = self._split_metadata(metadata)
        document_id = doc_id or uuid4()

        if original_job_id is not None:
            await self._assert_current_execution(original_job_id)

        await self._upsert_document(
            session_id=session_id,
            doc_id=document_id,
            source=source,
            url=url,
            document_fields=document_fields,
        )

        if original_job_id is not None:
            origin_key = self._build_origin_key(
                source=source,
                content=content,
                url=url,
                chunk_number=chunk_number,
            )
            statement = (
                insert(DocumentationChunk)
                .values(
                    session_id=session_id,
                    doc_id=document_id,
                    chunk_number=chunk_number,
                    scrape_job_ids=[str(original_job_id)],
                    origin_job_id=original_job_id,
                    origin_key=origin_key,
                    summary=summary,
                    content=content,
                    doc_metadata=chunk_metadata,
                )
                .on_conflict_do_update(
                    constraint="uq_doc_chunks_job_origin",
                    set_={
                        "doc_id": document_id,
                        "chunk_number": chunk_number,
                        "scrape_job_ids": case(
                            (
                                DocumentationChunk.scrape_job_ids.contains([str(original_job_id)]),
                                DocumentationChunk.scrape_job_ids,
                            ),
                            else_=DocumentationChunk.scrape_job_ids.concat([str(original_job_id)]),
                        ),
                        "summary": summary,
                        "content": content,
                        "metadata": chunk_metadata,
                    },
                )
                .returning(DocumentationChunk.chunk_id)
            )
            chunk_id = (await self.db.execute(statement)).scalar_one()
            await self.db.flush()
            logger.info("Upserted chunk_id %s for session %s and job %s", chunk_id, session_id, original_job_id)
            return chunk_id

        chunk = DocumentationChunk(
            session_id=session_id,
            doc_id=document_id,
            chunk_number=chunk_number,
            scrape_job_ids=[],
            summary=summary,
            content=content,
            doc_metadata=chunk_metadata,
        )
        self.db.add(chunk)
        await self.db.flush()
        logger.info(f"Created chunk_id {chunk.chunk_id} for session {session_id}")
        return chunk.chunk_id

    async def get_documentation_items_by_session(
        self, session_id: UUID, source: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all documentation chunks for a session.

        :param session_id: Session ID
        :param source: Optional source filter ('scraper' or 'upload')
        :return: List of documentation item dicts
        """
        query = self._chunks_with_document().where(DocumentationChunk.session_id == session_id)

        if source:
            query = query.where(Document.source == source)

        query = query.order_by(DocumentationChunk.created_at)

        result = await self.db.execute(query)
        chunks = result.scalars().unique().all()

        return [self._to_item_dict(chunk) for chunk in chunks]

    async def get_conndev_documentation_items_by_session(self, session_id: UUID) -> List[Dict[str, Any]]:
        """
        Get only the session's midPoint connector-development (conndev) documents.

        The content type is a property of the document, so the filter runs over
        the session's documents rather than over every one of its chunks. The SQL
        normalization mirrors ``normalize_content_type``: parameters after ``;``
        are dropped and the value is trimmed and lower-cased before comparison.

        :param session_id: Session ID
        :return: List of documentation item dicts, oldest first
        """
        normalized_content_type = func.lower(func.btrim(func.split_part(Document.content_type, ";", 1)))
        query = (
            self._chunks_with_document()
            .where(
                DocumentationChunk.session_id == session_id,
                normalized_content_type.in_(sorted(CONNDEV_CONTENT_TYPES)),
            )
            .order_by(DocumentationChunk.created_at)
        )

        result = await self.db.execute(query)
        chunks = result.scalars().unique().all()

        return [self._to_item_dict(chunk) for chunk in chunks]

    async def get_documentation_items_by_doc_id(self, session_id: UUID, doc_id: UUID) -> List[Dict[str, Any]]:
        """
        Get all chunks of one document in a session.

        :param session_id: Session ID
        :param doc_id: Document ID
        :return: List of documentation item dicts
        """
        query = (
            self._chunks_with_document()
            .where(
                DocumentationChunk.session_id == session_id,
                DocumentationChunk.doc_id == doc_id,
            )
            .order_by(DocumentationChunk.created_at, DocumentationChunk.chunk_id)
        )

        chunks = (await self.db.execute(query)).scalars().unique().all()
        return [self._to_export_item_dict(chunk) for chunk in chunks]

    async def get_documentation_items_for_export(self, session_id: UUID) -> List[Dict[str, Any]]:
        """
        Get documentation chunks for export for a specific session.

        :param session_id: Session ID
        :return: List of documentation item dicts
        """
        query = (
            self._chunks_with_document()
            .where(DocumentationChunk.session_id == session_id)
            .order_by(
                DocumentationChunk.doc_id,
                DocumentationChunk.created_at,
                DocumentationChunk.chunk_id,
            )
        )

        result = await self.db.execute(query)
        chunks = result.scalars().unique().all()

        return [self._to_export_item_dict(chunk) for chunk in chunks]

    async def get_scraped_documentation_items_for_export_by_job(
        self,
        session_id: UUID,
        job_id: UUID,
    ) -> List[Dict[str, Any]]:
        """Get every scraper chunk one job is responsible for, for that job's own result payload."""
        query = (
            self._chunks_with_document()
            .where(
                DocumentationChunk.session_id == session_id,
                or_(
                    DocumentationChunk.origin_job_id == job_id,
                    DocumentationChunk.scrape_job_ids.contains([str(job_id)]),
                ),
                Document.source == "scraper",
            )
            .order_by(
                DocumentationChunk.doc_id,
                DocumentationChunk.created_at,
                DocumentationChunk.chunk_id,
            )
        )

        chunks = (await self.db.execute(query)).scalars().unique().all()
        return [self._to_export_item_dict(chunk) for chunk in chunks]

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
        Import documentation chunks for a specific session preserving exported fields.

        :param session_id: Session ID
        :param items: Flat list of chunk dictionaries
        :return: Number of imported chunks
        """
        imported_count = 0
        for item in items:
            created_at_raw = item.get("createdAt")
            created_at = self._parse_iso_datetime(created_at_raw) if isinstance(created_at_raw, str) else None
            chunk_metadata, document_fields, chunk_number = self._split_metadata(item.get("metadata"))
            doc_id = UUID(str(item["docId"])) if item.get("docId") else uuid4()

            await self._upsert_document(
                session_id=session_id,
                doc_id=doc_id,
                source=str(item["source"]),
                url=item.get("url"),
                document_fields=document_fields,
            )

            chunk_kwargs: Dict[str, Any] = {
                "chunk_id": UUID(str(item["chunkId"])),
                "session_id": session_id,
                "doc_id": doc_id,
                "chunk_number": chunk_number,
                "scrape_job_ids": [str(job_id) for job_id in (item.get("scrapeJobIds") or [])],
                "summary": item.get("summary"),
                "content": str(item["content"]),
                "doc_metadata": chunk_metadata,
            }
            if created_at is not None:
                chunk_kwargs["created_at"] = created_at

            self.db.add(DocumentationChunk(**chunk_kwargs))
            imported_count += 1

        await self.db.flush()
        logger.info(f"Imported {imported_count} documentation chunks for session {session_id}")
        return imported_count

    async def get_documentation_items_by_session_and_job(self, session_id: UUID, job_id: UUID) -> List[Dict[str, Any]]:
        """
        Get documentation chunks for a session that are related to a specific job.

        :param session_id: Session ID
        :param job_id: Job ID to filter relevant documentation chunks
        :return: List of documentation item dicts
        """
        query = self._chunks_with_document().where(
            DocumentationChunk.session_id == session_id,
            DocumentationChunk.scrape_job_ids.contains([str(job_id)]),
        )

        result = await self.db.execute(query)
        chunks = result.scalars().unique().all()

        return [self._to_item_dict(chunk) for chunk in chunks]

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
        Update an existing documentation chunk, and its document where the change
        belongs there.

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
        query = (
            self._chunks_with_document()
            .where(DocumentationChunk.chunk_id == chunk_id)
            .with_for_update(of=DocumentationChunk)
        )
        result = await self.db.execute(query)
        chunk = result.scalars().unique().one_or_none()

        if chunk is None:
            logger.warning(f"Documentation chunk not found for update: {chunk_id}")
            return False

        if metadata is not None:
            chunk_metadata, document_fields, chunk_number = self._split_metadata(metadata)
            chunk.doc_metadata = chunk_metadata
            chunk.chunk_number = chunk_number
        else:
            document_fields = {}

        if doc_id is not None:
            chunk.doc_id = doc_id
        if content is not None:
            chunk.content = content
        if summary is not None:
            chunk.summary = summary
        if original_job_id is not None:
            current_ids = chunk.scrape_job_ids or []
            if str(original_job_id) not in current_ids:
                chunk.scrape_job_ids = current_ids + [str(original_job_id)]

        if source is not None or url is not None or doc_id is not None or document_fields:
            await self._upsert_document(
                session_id=chunk.session_id,
                doc_id=chunk.doc_id,
                source=source if source is not None else chunk.document.source,
                url=url if url is not None else chunk.document.url,
                document_fields=document_fields,
            )

        await self.db.flush()
        logger.info(f"Updated documentation chunk with chunk_id: {chunk_id}")
        return True

    async def remove_job_ids_from_documentation_items(self, session_id: UUID, doc_source: str) -> int:
        """
        Remove job IDs from documentation chunks of a specific source for a session.

        :param session_id: Session ID
        :param doc_source: Source type to filter chunks ('scraper' or 'upload')
        :return: Number of chunks updated
        """
        documents_of_source = (
            select(Document.doc_id)
            .where(Document.session_id == session_id, Document.source == doc_source)
            .scalar_subquery()
        )
        result = await self.db.execute(
            update(DocumentationChunk)
            .where(
                DocumentationChunk.session_id == session_id,
                DocumentationChunk.doc_id.in_(documents_of_source),
                DocumentationChunk.scrape_job_ids != text("'[]'::jsonb"),
            )
            .values(scrape_job_ids=[])
        )

        count = int(getattr(result, "rowcount", 0) or 0)
        await self.db.flush()
        logger.info(
            f"Removed job IDs from {count} documentation chunks for session {session_id} and source {doc_source}"
        )
        return count

    async def remove_documentation_items_by_doc_id(self, session_id: UUID, doc_id: UUID) -> int:
        """
        Remove one document of a session together with its chunks.

        :param session_id: Session ID
        :param doc_id: Document ID to remove
        :return: Number of chunks deleted
        """
        chunk_count = (
            await self.db.execute(
                select(func.count())
                .select_from(DocumentationChunk)
                .where(
                    DocumentationChunk.session_id == session_id,
                    DocumentationChunk.doc_id == doc_id,
                )
            )
        ).scalar_one()

        await self.db.execute(delete(Document).where(Document.session_id == session_id, Document.doc_id == doc_id))

        await self.db.flush()
        logger.info(f"Deleted document {doc_id} with {chunk_count} chunk(s) for session {session_id}")
        return int(chunk_count)

    async def get_documentation_item(self, chunk_id: UUID) -> Optional[Dict[str, Any]]:
        """
        Get a single documentation chunk by ID.

        :param chunk_id: Documentation chunk ID
        :return: Documentation item dict or None
        """
        query = self._chunks_with_document().where(DocumentationChunk.chunk_id == chunk_id)
        result = await self.db.execute(query)
        chunk = result.scalars().unique().one_or_none()

        if chunk is None:
            return None

        return {**self._to_item_dict(chunk), "sessionId": str(chunk.session_id)}

    async def delete_documentation_items_by_session(self, session_id: UUID) -> int:
        """
        Delete all documentation of a session.

        :param session_id: Session ID
        :return: Number of chunks deleted
        """
        chunk_count = (
            await self.db.execute(
                select(func.count()).select_from(DocumentationChunk).where(DocumentationChunk.session_id == session_id)
            )
        ).scalar_one()

        await self.db.execute(delete(Document).where(Document.session_id == session_id))

        await self.db.flush()
        logger.info(f"Deleted {chunk_count} documentation chunks for session {session_id}")
        return int(chunk_count)
