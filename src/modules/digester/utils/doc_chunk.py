# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Any, Dict, List, Set, Tuple

from ....common.chunks import normalize_to_text

logger = logging.getLogger(__name__)


def select_doc_chunks(
    doc_items: List[dict], relevant_chunks: List[Dict[str, Any]], log_prefix: str
) -> Tuple[List[str], List[str]]:
    """
    TODO
    """
    wanted: Set[str] = set()
    for rc in relevant_chunks:
        chunk_id = str(rc.get("chunk_id") or rc.get("chunkId") or "").strip()
        if chunk_id:
            wanted.add(chunk_id)

    if not wanted:
        logger.info("[%s] No chunk_id found in relevant_documentations", log_prefix)
        return [], []

    logger.info("[%s] Selecting %d doc chunks by chunk_id", log_prefix, len(wanted))

    selected_chunks: List[str] = []
    selected_chunk_ids: List[str] = []

    for item in doc_items:
        chunk_id = str(item.get("chunkId", "")).strip()
        if not chunk_id or chunk_id not in wanted:
            continue

        # chunk_id == one chunk; keep content as-is (normalize only)
        selected_chunks.append(normalize_to_text(item.get("content", "")))
        selected_chunk_ids.append(chunk_id)

    return selected_chunks, selected_chunk_ids
