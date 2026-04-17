#  Copyright (C) 2010-2026 Evolveum and contributors
#  #
#  Licensed under the EUPL-1.2 or later.

import copy
import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)


def normalize_url(value: Any) -> str:
    """Normalize URL-like values for reliable comparisons."""
    return str(value).rstrip("/") if value else ""


def normalize_object_class_name(object_class: str) -> str:
    """Normalize object class name for case-insensitive matching."""
    return object_class.strip().lower()


def normalize_chunk_pair(chunk: Mapping[str, Any]) -> tuple[str, str] | None:
    """Normalize one chunk reference dict to (doc_id, chunk_id) pair."""
    if not isinstance(chunk, Mapping):
        return None

    doc_id = chunk.get("docId") or chunk.get("doc_id")
    chunk_id = chunk.get("chunkId") or chunk.get("chunk_id")
    if not doc_id or not chunk_id:
        return None
    return str(doc_id), str(chunk_id)


def normalize_endpoint_key(path: Any, method: Any) -> tuple[str, str] | None:
    """Build normalized endpoint key from path + method."""
    path_str = str(path or "").strip()
    method_str = str(method or "").strip().upper()
    if not path_str or not method_str:
        return None
    return path_str, method_str


def normalize_relevant_chunks_for_session(value: Any) -> Any:
    """
    Normalize relevant chunk references for session storage.

    Converts dict entries to camelCase shape: {"docId": "...", "chunkId": "..."}.
    """
    if not isinstance(value, list):
        return value

    if value and all(isinstance(item, int) for item in value):
        return value

    normalized: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        pair = normalize_chunk_pair(item)
        if pair is None:
            continue
        doc_id, chunk_id = pair
        normalized.append({"docId": doc_id, "chunkId": chunk_id})
    return normalized


def normalize_input(input_payload: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize job input for better querying
    """
    normalized_input = copy.deepcopy(input_payload)
    # Remove fields that are not relevant or harmful for job uniqueness checks
    if "sessionId" in normalized_input:
        normalized_input.pop("sessionId")
    if "session_id" in normalized_input:
        normalized_input.pop("session_id")
    if "doc_id" in normalized_input:
        normalized_input.pop("doc_id")
    if "usePreviousSessionData" in normalized_input:
        normalized_input.pop("usePreviousSessionData")
    if "chunks" in normalized_input:
        normalized_input["chunks"] = sorted(
            normalized_input["chunks"], key=lambda x: x[0] if isinstance(x, tuple) and len(x) > 0 else ""
        )
    if "documentationItems" in normalized_input:
        for doc_item in normalized_input["documentationItems"]:
            if isinstance(doc_item, dict):
                if "chunkId" in doc_item:
                    doc_item.pop("chunkId")
                if "chunk_id" in doc_item:
                    doc_item.pop("chunk_id")
                if "docId" in doc_item:
                    doc_item.pop("docId")
                if "doc_id" in doc_item:
                    doc_item.pop("doc_id")
                if "session_id" in doc_item:
                    doc_item.pop("session_id")
                if "scrape_job_ids" in doc_item:
                    doc_item.pop("scrape_job_ids")
                if "scrapeJobIds" in doc_item:
                    doc_item.pop("scrapeJobIds")
        normalized_input["documentationItems"] = sorted(
            normalized_input["documentationItems"],
            key=lambda x: (
                (str(x.get("url") or ""), str(x.get("summary") or "")) if isinstance(x, Mapping) else (str(x), "")
            ),
        )
    if "relevantObjectClasses" in normalized_input and "objectClasses" in normalized_input["relevantObjectClasses"]:
        for obj_class in normalized_input["relevantObjectClasses"]["objectClasses"]:
            obj_class.pop("relevantDocumentations")
    if "relevantDocumentations" in normalized_input:
        normalized_input.pop("relevantDocumentations")
    return normalized_input
