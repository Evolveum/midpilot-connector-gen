# Copyright (c) 2025 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Helper functions for extracting and processing document metadata."""

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
        summary = doc_summary

    # Extract and format tags
    metadata = doc_metadata.get("@metadata", {})
    llm_tags = metadata.get("llm_tags")
    if llm_tags:
        if isinstance(llm_tags, list):
            tags = ", ".join(str(tag) for tag in llm_tags)
        else:
            tags = str(llm_tags)

    return summary, tags
