# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Relevant-chunk selection for codegen.

Loads and normalizes the relevant documentation chunk references stored per
result key (attributes/endpoints/auth/object classes) into the ordered reference
lists consumed by the Groovy generators. Pure selection logic — no generation.
"""

import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from uuid import UUID

from src.core.db import async_session_maker
from src.database.repositories.relevant_chunk_repository import RelevantChunkRepository
from src.documents.normalize import normalize_object_class_name
from src.modules.codegen.schema import AuthPayload
from src.modules.codegen.selection.authorization import (
    has_matching_preferred_authorization,
    select_authorization_chunk_refs,
)

logger = logging.getLogger(__name__)


def _collect_pairs(val: Any) -> List[Tuple[int, Optional[str]]]:
    """
    Normalize relevant chunk references to ordered (index, chunk_id) tuples.
    """
    out: List[Tuple[int, Optional[str]]] = []
    if not val:
        return out
    if isinstance(val, list) and val:
        first = val[0]
        if isinstance(first, dict):
            for item in val:
                if not isinstance(item, dict):
                    continue
                chunk_id = item.get("chunk_id") or item.get("chunkId")
                if isinstance(chunk_id, str):
                    raw_sequence = item.get("relevant_sequence") or item.get("relevantSequence")
                    if raw_sequence is None:
                        sequence = len(out)
                    else:
                        try:
                            sequence = int(raw_sequence)
                        except Exception:
                            sequence = len(out)
                    out.append((sequence, chunk_id))
        else:
            for idx in val:
                if isinstance(idx, int):
                    out.append((idx, None))
    return out


def _merge_unique_pairs(*seqs: Iterable[Tuple[int, Optional[str]]]) -> List[Tuple[int, Optional[str]]]:
    """
    Merge multiple (idx, uuid) sequences preserving unique chunk IDs.

    When a chunk_id is present, deduplicate by chunk_id only so the same
    documentation chunk is not processed multiple times if it was selected
    from both attributes and endpoints. For legacy index-only entries
    (chunk_id is None), preserve uniqueness by the full pair.
    """
    merged: List[Tuple[int, Optional[str]]] = []
    seen_pairs: set[Tuple[int, Optional[str]]] = set()
    seen_chunk_ids: set[str] = set()
    for seq in seqs:
        for idx, chunk_id in seq:
            if isinstance(chunk_id, str):
                if chunk_id in seen_chunk_ids:
                    continue
                seen_chunk_ids.add(chunk_id)
                merged.append((idx, chunk_id))
                continue

            pair = (idx, chunk_id)
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                merged.append(pair)
    return merged


async def _collect_relation_object_class_pairs(
    session_id: UUID,
    object_class_names: Sequence[str],
) -> List[Dict[str, str]]:
    """
    Select object-class documentation chunks for the classes a relation is built from.

    Which classes those are is decided by the caller: besides the subject and the object it
    can include the class carrying the association, whose documentation holds the endpoints
    implementing the link and appears in neither end's own documentation.
    """
    if not object_class_names:
        return []

    async with async_session_maker() as db:
        repo = RelevantChunkRepository(db)
        chunk_map = await repo.get_relevant_chunks_grouped_by_entity(
            session_id=session_id,
            result_key="objectClassesOutput",
        )

    selected_chunks: List[Dict[str, str]] = []
    seen_chunk_ids: set[str] = set()

    for class_name in object_class_names:
        class_key = normalize_object_class_name(class_name)
        relevant_refs = chunk_map.get(class_key, [])
        if not relevant_refs:
            logger.warning("[Codegen:Relation] No relevant chunks found for object class %s", class_name)
            continue

        for chunk in relevant_refs:
            chunk_id = str(chunk.get("chunkId") or chunk.get("chunk_id") or "")
            doc_id = str(chunk.get("docId") or chunk.get("doc_id") or "")
            if not chunk_id or not doc_id:
                continue
            if chunk_id in seen_chunk_ids:
                continue
            seen_chunk_ids.add(chunk_id)
            selected_chunks.append({"doc_id": doc_id, "chunk_id": chunk_id})

    return selected_chunks


async def _collect_relevant_chunks(
    session_id: UUID, object_class: str, operation_name: str
) -> Optional[List[Dict[str, Any]]]:
    """
    Collect relevant chunk references from the session for a given object class.

    Args:
        session_id: Session UUID
        object_class: Object class name
        operation_name: Operation name for logging (e.g., "Search", "Create")

    Returns:
        Ordered relevant chunk references, or ``None`` when no selection exists.
    """
    key_endpoints = f"{object_class}EndpointsOutput"
    key_attributes = f"{object_class}AttributesOutput"
    async with async_session_maker() as db:
        repo = RelevantChunkRepository(db)
        relevant_map = await repo.get_relevant_chunks_map(session_id, result_keys=[key_endpoints, key_attributes])

    endpoint_refs = relevant_map.get(key_endpoints, [])
    attribute_refs = relevant_map.get(key_attributes, [])

    pairs_endpoints = _collect_pairs(endpoint_refs)
    pairs_attributes = _collect_pairs(attribute_refs)
    merged_pairs = _merge_unique_pairs(pairs_endpoints, pairs_attributes)

    if not merged_pairs:
        return None

    chunk_to_doc: Dict[str, str] = {}
    for chunk in [*endpoint_refs, *attribute_refs]:
        if not isinstance(chunk, dict):
            continue
        chunk_id = chunk.get("chunk_id") or chunk.get("chunkId")
        doc_id = chunk.get("doc_id") or chunk.get("docId")
        if isinstance(chunk_id, str) and isinstance(doc_id, str) and chunk_id not in chunk_to_doc:
            chunk_to_doc[chunk_id] = doc_id

    relevant_pairs = [
        {"chunk_id": chunk_id, "doc_id": chunk_to_doc[chunk_id]} if chunk_id in chunk_to_doc else {"chunk_id": chunk_id}
        for _, chunk_id in merged_pairs
        if chunk_id
    ]

    logger.info(
        "[Codegen:%s] Relevant chunks for endpoints=%d, for attributes=%d, merged=%d for %s",
        operation_name,
        len(pairs_endpoints),
        len(pairs_attributes),
        len(merged_pairs),
        object_class,
    )

    return relevant_pairs


async def _collect_authorization_relevant_chunks(
    session_id: UUID,
    auth_payload: AuthPayload,
    preferred_authorizations: Optional[List[Dict[str, Any]]],
) -> Optional[List[Dict[str, Any]]]:
    async with async_session_maker() as db:
        repo = RelevantChunkRepository(db)
        relevant_map = await repo.get_relevant_chunks_map(session_id, result_keys=["authOutput"])

    auth_pairs = select_authorization_chunk_refs(relevant_map, auth_payload, preferred_authorizations)
    if not auth_pairs:
        if preferred_authorizations and not has_matching_preferred_authorization(
            auth_payload, preferred_authorizations
        ):
            logger.info("[Codegen:Authorization] No selected authorization was identified in analyzed auth output")
            return []
        return None

    logger.info(
        "[Codegen:Authorization] Relevant auth chunks selected=%d preferred=%s",
        len(auth_pairs),
        bool(preferred_authorizations),
    )
    return auth_pairs
