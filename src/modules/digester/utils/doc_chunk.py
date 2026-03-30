# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Any, Dict, List, Set, Tuple

from src.common.chunks import normalize_to_text

logger = logging.getLogger(__name__)


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
    wanted_chunk_ids: Set[str] = {
        chunk_id for rc in relevant_chunks if (chunk_id := str(rc.get("chunk_id") or rc.get("chunkId") or "").strip())
    }

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
