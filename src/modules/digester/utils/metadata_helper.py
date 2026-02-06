# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any, Dict, Tuple


def extract_summary_and_tags(doc_metadata: Dict[str, Any] | None) -> Tuple[str, str]:
    """
    Extract summary and tags from document metadata.

    Args:
        doc_metadata: Document metadata dictionary containing 'summary' and '@metadata' fields

    Returns:
        Tuple of (summary, tags) as strings. Empty strings if not available.
    """
    summary = ""
    tags = ""

    if not doc_metadata:
        return summary, tags

    # Extract summary
    doc_summary = doc_metadata.get("summary")
    if doc_summary:
        summary = str(doc_summary)

    # Extract and format tags
    metadata = doc_metadata.get("@metadata", {}) or {}
    llm_tags = metadata.get("llm_tags")
    if llm_tags:
        if isinstance(llm_tags, list):
            tags = ", ".join(str(tag) for tag in llm_tags)
        else:
            tags = str(llm_tags)

    return summary, tags


def build_doc_metadata_map(doc_items: list[dict]) -> dict[str, dict[str, Any]]:
    """
    Build a lookup map for doc metadata:
        doc_uuid (str) -> {"summary": ..., "@metadata": {...}}

    This avoids repeatedly scanning doc_items in O(n) per document.

    Notes:
        - Uses string UUID keys because the doc_items usually store UUIDs as strings.
        - Missing '@metadata' becomes {}.
    """
    out: dict[str, dict[str, Any]] = {}

    for item in doc_items:
        doc_uuid = item.get("uuid")
        if not doc_uuid:
            continue

        out[str(doc_uuid)] = {
            "summary": item.get("summary"),
            "@metadata": item.get("@metadata", {}) or {},
        }

    return out
