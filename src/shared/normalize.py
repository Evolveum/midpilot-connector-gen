#  Copyright (C) 2010-2026 Evolveum and contributors
#  #
#  Licensed under the EUPL-1.2 or later.

import copy
import logging
from collections.abc import Iterable, Mapping
from typing import Any

from src.shared.coerce import as_dict_list, as_list, as_mapping

logger = logging.getLogger(__name__)


def normalize_url(value: Any) -> str:
    """Normalize URL-like values for reliable comparisons."""
    return str(value).rstrip("/") if value else ""


def normalize_chunk_pair(chunk: Mapping[str, Any]) -> tuple[str, str] | None:
    """Normalize one chunk reference dict to (doc_id, chunk_id) pair."""
    if not isinstance(chunk, Mapping):
        return None

    doc_id = chunk.get("docId") or chunk.get("doc_id")
    chunk_id = chunk.get("chunkId") or chunk.get("chunk_id")
    if not doc_id or not chunk_id:
        return None
    return str(doc_id), str(chunk_id)


def normalize_relevant_sequence(value: Any) -> dict[str, str]:
    """Normalize a relevant-sequence payload to camelCase ``{startSequence, endSequence}``.

    Accepts snake_case or camelCase keys. Returns ``{}`` when either boundary is missing.
    """
    value = as_mapping(value)
    start_sequence = value.get("start_sequence") or value.get("startSequence")
    end_sequence = value.get("end_sequence") or value.get("endSequence")
    if not start_sequence or not end_sequence:
        return {}
    return {
        "startSequence": str(start_sequence),
        "endSequence": str(end_sequence),
    }


def build_relevant_documentations(pairs: Iterable[tuple[str, str]]) -> list[dict[str, str]]:
    """Build a sorted, deduplicated camelCase ``relevantDocumentations`` list from ``(doc_id, chunk_id)`` pairs."""
    return [
        {"docId": doc_id, "chunkId": chunk_id}
        for doc_id, chunk_id in sorted(set(pairs), key=lambda pair: (pair[0], pair[1]))
    ]


def normalize_relevant_documentation_refs(value: Any) -> list[dict[str, str]]:
    """
    Normalize a ``relevantDocumentations`` payload to a list of ``{"chunk_id", "doc_id"}`` refs.

    Accepts the loose shapes that reach pydantic validators and stored payloads (snake_case or
    camelCase keys, non-list / non-dict noise) and keeps only refs that carry both ids.
    """
    refs: list[dict[str, str]] = []
    for chunk in as_list(value):
        pair = normalize_chunk_pair(chunk)
        if pair is None:
            continue
        doc_id, chunk_id = pair
        refs.append({"chunk_id": chunk_id, "doc_id": doc_id})
    return refs


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
    if "skipCache" in normalized_input:
        normalized_input.pop("skipCache")
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
    relevant_object_classes = normalized_input.get("relevantObjectClasses")
    if isinstance(relevant_object_classes, Mapping):
        object_classes = relevant_object_classes.get("objectClasses")
        for obj_class in as_dict_list(object_classes):
            obj_class.pop("relevantDocumentations", None)
            obj_class.pop("relevant_chunk_indices", None)
    if "relevantDocumentations" in normalized_input:
        normalized_input.pop("relevantDocumentations")
    return normalized_input
