# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple
from uuid import UUID

from src.documents.chunking import normalize_to_text
from src.modules.digester.schemas.common import ChunkReference, build_chunk_references_from_doc_items

__all__ = [
    "build_chunk_id_to_doc_id",
    "build_chunk_references_from_doc_items",
    "build_chunk_references_from_mappings",
    "build_relevant_chunks_from_doc_items",
    "chunk_ids_from_relevant_chunks",
    "collect_relevant_chunks",
    "exclude_doc_items_by_chunk_id",
    "resolve_relevant_chunk_ref",
    "select_doc_chunks",
]

logger = logging.getLogger(__name__)


def resolve_relevant_chunk_ref(
    chunk_id: Any,
    chunk_id_to_doc_id: Mapping[str, str],
    logger_scope: str,
) -> Optional[Dict[str, str]]:
    """Resolve a chunk id to a ``{"doc_id", "chunk_id"}`` reference.

    Returns ``None`` (and logs a warning) when no ``docId`` is known for the chunk, so
    callers can skip the mapping without repeating the lookup-and-warn boilerplate.
    """
    chunk_id_str = str(chunk_id)
    doc_id = chunk_id_to_doc_id.get(chunk_id_str)
    if not doc_id:
        logger.warning("[%s] Missing docId for chunk %s, skipping relevant chunk mapping", logger_scope, chunk_id_str)
        return None
    return {"doc_id": doc_id, "chunk_id": chunk_id_str}


def collect_relevant_chunks(
    results: Iterable[Tuple[Any, bool, UUID]],
    chunk_id_to_doc_id: Mapping[str, str],
    logger_scope: str,
) -> List[Dict[str, str]]:
    """Map ``(result, has_relevant_data, chunk_id)`` extractor tuples to relevant chunk refs.

    Keeps only chunks flagged relevant and resolvable to a ``docId`` (see
    :func:`resolve_relevant_chunk_ref`).
    """
    relevant_chunks: List[Dict[str, str]] = []
    for _result, has_relevant_data, chunk_id in results:
        if not has_relevant_data:
            continue
        chunk_ref = resolve_relevant_chunk_ref(chunk_id, chunk_id_to_doc_id, logger_scope)
        if chunk_ref is not None:
            relevant_chunks.append(chunk_ref)
    return relevant_chunks


def build_chunk_id_to_doc_id(chunk_items: List[dict]) -> Dict[str, str]:
    """Build chunk_id -> doc_id mapping from documentation items."""
    return {ref.chunk_id: ref.doc_id for ref in build_chunk_references_from_doc_items(chunk_items)}


def build_relevant_chunks_from_doc_items(chunk_items: List[dict]) -> List[Dict[str, Any]]:
    """Build relevant chunk descriptors from filtered documentation items."""
    return [chunk_ref.to_internal_dict() for chunk_ref in build_chunk_references_from_doc_items(chunk_items)]


def build_chunk_references_from_mappings(chunks: List[Dict[str, Any]]) -> List[ChunkReference]:
    """Normalize mixed doc_id/docId and chunk_id/chunkId mappings."""
    chunk_refs: List[ChunkReference] = []
    seen_pairs: set[tuple[str, str]] = set()
    for chunk in chunks:
        raw_doc_id = chunk.get("doc_id") or chunk.get("docId")
        raw_chunk_id = chunk.get("chunk_id") or chunk.get("chunkId")
        if not raw_doc_id or not raw_chunk_id:
            continue

        pair = (str(raw_doc_id).strip(), str(raw_chunk_id).strip())
        if not pair[0] or not pair[1] or pair in seen_pairs:
            continue

        chunk_refs.append(ChunkReference(doc_id=pair[0], chunk_id=pair[1]))
        seen_pairs.add(pair)

    return chunk_refs


def chunk_ids_from_relevant_chunks(relevant_chunks: List[Dict[str, Any]]) -> set[str]:
    return {
        chunk_id
        for chunk in relevant_chunks
        if (chunk_id := str(chunk.get("chunk_id") or chunk.get("chunkId") or "").strip())
    }


def exclude_doc_items_by_chunk_id(chunk_items: List[dict], excluded_chunk_ids: set[str]) -> List[dict]:
    if not excluded_chunk_ids:
        return chunk_items
    return [item for item in chunk_items if str(item.get("chunkId") or "").strip() not in excluded_chunk_ids]


def select_doc_chunks(
    doc_items: List[dict], relevant_chunks: List[Dict[str, Any]], log_prefix: str
) -> Tuple[List[str], List[str]]:
    """
    Select documentation chunk contents by matching `chunkId` against relevant chunk IDs

    Args:
        doc_items: Documentation items containing at least `chunkId` and `content`
        relevant_chunks: Relevant chunk descriptors containing `chunk_id`
        log_prefix: Prefix used in log messages for easier traceability.

    Returns:
        A tuple with:
        - selected_chunks_content: Normalized text content of matched chunks.
        - selected_chunk_ids: `chunkId` values for the matched chunks, in iteration order.
    """
    wanted_chunk_ids: Set[str] = chunk_ids_from_relevant_chunks(relevant_chunks)

    if not wanted_chunk_ids:
        logger.info("[%s] No chunk_id found in relevant_documentations", log_prefix)
        return [], []

    logger.info("[%s] Selecting %d doc chunks by chunk_id", log_prefix, len(wanted_chunk_ids))

    selected_chunks_content: List[str] = []
    selected_chunk_ids: List[str] = []

    for item in doc_items:
        chunk_id = str(item.get("chunkId") or "").strip()
        if chunk_id not in wanted_chunk_ids:
            continue

        selected_chunks_content.append(normalize_to_text(item.get("content", "")))
        selected_chunk_ids.append(chunk_id)

    return selected_chunks_content, selected_chunk_ids
