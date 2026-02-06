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
    Select chunk texts from doc_items by docUuid.

    Input relevant_chunks format:
      [{"docUuid": "<uuid>"}, ...]

    Returns:
      - selected_chunks: list[str] (chunk texts)
      - selected_doc_uuids: list[str] aligned with selected_chunks
    """
    wanted: Set[str] = set()
    for rc in relevant_chunks:
        doc_uuid = str(rc.get("docUuid", "")).strip()
        if doc_uuid:
            wanted.add(doc_uuid)

    if not wanted:
        logger.info("[%s] No docUuid found in relevant_chunks", log_prefix)
        return [], []

    logger.info("[%s] Selecting %d doc chunks by docUuid", log_prefix, len(wanted))

    selected_chunks: List[str] = []
    selected_doc_uuids: List[str] = []

    for item in doc_items:
        doc_uuid = str(item.get("uuid", "")).strip()
        if not doc_uuid or doc_uuid not in wanted:
            continue

        # doc_uuid == one chunk; keep content as-is (normalize only)
        selected_chunks.append(normalize_to_text(item.get("content", "")))
        selected_doc_uuids.append(doc_uuid)

    return selected_chunks, selected_doc_uuids
