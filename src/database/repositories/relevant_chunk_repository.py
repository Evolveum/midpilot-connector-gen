# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
import logging
from typing import Any, Dict, Iterable, List, Mapping, Optional
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import DocumentationChunk, RelevantChunk
from src.shared.normalize import normalize_relevant_sequence

logger = logging.getLogger(__name__)


class RelevantChunkRepository:
    """Repository for relevant chunk data access operations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _parse_uuid(value: Any) -> Optional[UUID]:
        if value is None:
            return None
        try:
            return UUID(str(value))
        except Exception:
            return None

    @staticmethod
    def _normalize_entity_key(value: Any) -> Optional[str]:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    def _normalize_chunk(
        self,
        chunk_info: Mapping[str, Any],
        *,
        default_result_key: Optional[str],
        default_entity_key: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        result_key = str(
            chunk_info.get("result_key") or chunk_info.get("resultKey") or default_result_key or ""
        ).strip()
        if not result_key:
            return None

        entity_key = self._normalize_entity_key(
            chunk_info.get("entity_key") or chunk_info.get("entityKey") or default_entity_key
        )

        chunk_id = self._parse_uuid(chunk_info.get("chunk_id") or chunk_info.get("chunkId"))
        if not chunk_id:
            return None
        claimed_doc_id = self._parse_uuid(chunk_info.get("doc_id") or chunk_info.get("docId"))

        raw_sequence = chunk_info.get("relevant_sequence") or chunk_info.get("relevantSequence")
        if not raw_sequence:
            start_sequence = chunk_info.get("start_sequence") or chunk_info.get("startSequence")
            end_sequence = chunk_info.get("end_sequence") or chunk_info.get("endSequence")
            if start_sequence and end_sequence:
                raw_sequence = {
                    "startSequence": start_sequence,
                    "endSequence": end_sequence,
                }
        relevant_sequence = normalize_relevant_sequence(raw_sequence)

        return {
            "result_key": result_key,
            "entity_key": entity_key,
            "claimed_doc_id": claimed_doc_id,
            "chunk_id": chunk_id,
            "relevant_sequence": relevant_sequence,
        }

    @staticmethod
    def _serialize_relevant_sequence(chunk: RelevantChunk) -> Dict[str, Any]:
        """Return ``{"relevantSequence": ...}`` for a row that carries both boundaries, else ``{}``."""
        sequence = chunk.relevant_sequence or {}
        if isinstance(sequence, dict) and sequence.get("startSequence") and sequence.get("endSequence"):
            return {
                "relevantSequence": {
                    "startSequence": str(sequence["startSequence"]),
                    "endSequence": str(sequence["endSequence"]),
                }
            }
        return {}

    @classmethod
    def _serialize_chunk(cls, chunk: RelevantChunk) -> Dict[str, Any]:
        """Serialize one relevant chunk, including the result key it was extracted for."""
        payload: Dict[str, Any] = {
            "resultKey": chunk.result_key,
            "docId": str(chunk.doc_id),
            "chunkId": str(chunk.chunk_id),
        }
        if chunk.entity_key:
            payload["entityKey"] = chunk.entity_key

        payload.update(cls._serialize_relevant_sequence(chunk))
        return payload

    @classmethod
    def _serialize_chunk_ref(cls, chunk: RelevantChunk) -> Dict[str, Any]:
        """Serialize one relevant chunk without ``resultKey``, for payloads already grouped by result."""
        return {key: value for key, value in cls._serialize_chunk(chunk).items() if key != "resultKey"}

    async def _load_session_doc_ids(self, session_id: UUID, chunk_ids: set[UUID]) -> Dict[UUID, UUID]:
        """Map chunk ids to the document they actually belong to, within one session.

        Relevance references come out of an LLM, so the document id they carry is
        only a claim. Resolving it against the stored documentation in a single
        query keeps the persisted triple consistent with what the composite
        foreign key on ``relevant_chunks`` enforces.
        """
        if not chunk_ids:
            return {}

        rows = (
            await self.db.execute(
                select(DocumentationChunk.chunk_id, DocumentationChunk.doc_id).where(
                    DocumentationChunk.session_id == session_id,
                    DocumentationChunk.chunk_id.in_(chunk_ids),
                )
            )
        ).all()
        return {chunk_id: doc_id for chunk_id, doc_id in rows}

    async def replace_relevant_chunks_for_result(
        self,
        *,
        session_id: UUID,
        result_key: str,
        chunks: List[Dict[str, Any]],
    ) -> int:
        """Replace all relevant chunk records for one (session_id, result_key)."""
        await self.db.execute(
            delete(RelevantChunk).where(
                RelevantChunk.session_id == session_id,
                RelevantChunk.result_key == result_key,
            )
        )

        if not chunks:
            await self.db.flush()
            return 0

        normalized: List[Dict[str, Any]] = []
        dedupe_keys: set[tuple[str, str, str, str]] = set()

        for chunk_info in chunks:
            if not isinstance(chunk_info, Mapping):
                continue

            normalized_chunk = self._normalize_chunk(
                chunk_info,
                default_result_key=result_key,
                default_entity_key=None,
            )
            if not normalized_chunk:
                continue

            dedupe_key = (
                normalized_chunk["result_key"],
                normalized_chunk["entity_key"] or "",
                str(normalized_chunk["chunk_id"]),
                json.dumps(normalized_chunk["relevant_sequence"], sort_keys=True),
            )
            if dedupe_key in dedupe_keys:
                continue

            dedupe_keys.add(dedupe_key)
            normalized.append(normalized_chunk)

        doc_ids_by_chunk = await self._load_session_doc_ids(session_id, {item["chunk_id"] for item in normalized})

        persisted = 0
        for item in normalized:
            stored_doc_id = doc_ids_by_chunk.get(item["chunk_id"])
            if stored_doc_id is None:
                logger.warning(
                    "Dropping relevant chunk %s for session %s result %s: the chunk is not part of "
                    "this session's documentation",
                    item["chunk_id"],
                    session_id,
                    item["result_key"],
                )
                continue
            claimed_doc_id = item["claimed_doc_id"]
            if claimed_doc_id is not None and claimed_doc_id != stored_doc_id:
                logger.warning(
                    "Relevant chunk %s for session %s result %s referenced document %s but belongs "
                    "to document %s; storing the document it belongs to",
                    item["chunk_id"],
                    session_id,
                    item["result_key"],
                    claimed_doc_id,
                    stored_doc_id,
                )

            self.db.add(
                RelevantChunk(
                    session_id=session_id,
                    result_key=item["result_key"],
                    entity_key=item["entity_key"],
                    doc_id=stored_doc_id,
                    chunk_id=item["chunk_id"],
                    relevant_sequence=item["relevant_sequence"],
                )
            )
            persisted += 1

        await self.db.flush()
        return persisted

    async def get_relevant_chunks(
        self,
        *,
        session_id: UUID,
        result_key: Optional[str] = None,
        entity_key: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get relevant chunks for a session, optionally filtered by result and entity key."""
        stmt = select(RelevantChunk).where(RelevantChunk.session_id == session_id)
        if result_key:
            stmt = stmt.where(RelevantChunk.result_key == result_key)

        normalized_entity_key = self._normalize_entity_key(entity_key)
        if entity_key is not None:
            if normalized_entity_key is None:
                stmt = stmt.where(RelevantChunk.entity_key.is_(None))
            else:
                stmt = stmt.where(RelevantChunk.entity_key == normalized_entity_key)

        stmt = stmt.order_by(
            RelevantChunk.result_key,
            RelevantChunk.entity_key.is_(None),
            RelevantChunk.entity_key,
            RelevantChunk.created_at,
        )

        rows = (await self.db.execute(stmt)).scalars().all()
        return [self._serialize_chunk(row) for row in rows]

    async def get_relevant_chunks_map(
        self,
        session_id: UUID,
        result_keys: Optional[Iterable[str]] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Return mapping result_key -> relevant chunks list."""
        stmt = select(RelevantChunk).where(RelevantChunk.session_id == session_id)
        keys: Optional[List[str]] = list(result_keys) if result_keys is not None else None
        if keys is not None:
            if not keys:
                return {}
            stmt = stmt.where(RelevantChunk.result_key.in_(keys))

        stmt = stmt.order_by(
            RelevantChunk.result_key,
            RelevantChunk.entity_key.is_(None),
            RelevantChunk.entity_key,
            RelevantChunk.created_at,
        )

        rows = (await self.db.execute(stmt)).scalars().all()
        mapping: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            mapping.setdefault(row.result_key, []).append(self._serialize_chunk_ref(row))

        return mapping

    async def get_relevant_chunks_grouped_by_entity(
        self,
        *,
        session_id: UUID,
        result_key: str,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Return mapping entity_key -> relevant chunks list for one result_key."""
        stmt = (
            select(RelevantChunk)
            .where(
                RelevantChunk.session_id == session_id,
                RelevantChunk.result_key == result_key,
            )
            .order_by(
                RelevantChunk.entity_key.is_(None),
                RelevantChunk.entity_key,
                RelevantChunk.created_at,
            )
        )
        rows = (await self.db.execute(stmt)).scalars().all()
        mapping: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            payload: Dict[str, Any] = {
                "docId": str(row.doc_id),
                "chunkId": str(row.chunk_id),
                **self._serialize_relevant_sequence(row),
            }
            mapping.setdefault(row.entity_key or "", []).append(payload)
        return mapping
