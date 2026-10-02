#  Copyright (C) 2010-2026 Evolveum and contributors
#  #
#  Licensed under the EUPL-1.2 or later.

import copy
import hashlib
import json
import logging
from collections.abc import Iterable, Mapping
from typing import Any

from src.shared.coerce import as_dict_list, as_list, as_mapping
from src.shared.json_values import to_jsonable

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


DOCUMENTATION_SELECTION_INPUT_KEY = "documentationSelection"
"""Job-input key of a stored documentation selection (``src.documents.selection``)."""

ORDER_SENSITIVE_SELECTION_ROLES = frozenset({"scimBaseline", "sqlSchema"})
"""Selection roles whose consumers resolve conflicting chunks by position.

The SCIM baseline lets the last definition of a schema, resource, ConnId class or
ServiceProviderConfig win; SQL schema collection lets the first definition of a table
win. The same chunks in another order can therefore produce a different result, so a
chunk's position in these roles is part of its cache identity. Primary and fallback
chunks feed independent per-chunk LLM extraction and stay order-insensitive.
"""

_SELECTION_STRUCTURE_KEYS = frozenset({"version", "chunks"})
_SELECTION_CHUNK_IDENTIFIER_KEYS = frozenset({"chunkId", "docId"})


def documentation_selection_chunk_identities(selection: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """
    Return ``(chunkId, identity)`` for every chunk of a stored documentation selection.

    The identity is the stored chunk without its database identifiers, the sorted roles
    that reference it (every key other than ``version``/``chunks`` is a role holding
    ``{docId, chunkId}`` references) and, for :data:`ORDER_SENSITIVE_SELECTION_ROLES`,
    its position in that role's reference list. Two chunks with equal identities are
    interchangeable for an extraction: same text, same extraction metadata, same part
    in the same attempts at the same position where position decides. Both the cache
    fingerprint and the cache remap derive from this one definition, so they cannot
    disagree about what "the same chunk" means.
    """
    roles_by_chunk: dict[str, set[str]] = {}
    positions_by_chunk: dict[str, dict[str, int]] = {}
    for role, references in selection.items():
        if role in _SELECTION_STRUCTURE_KEYS:
            continue
        role_name = str(role)
        position = 0
        for reference in as_list(references):
            pair = normalize_chunk_pair(reference)
            if pair is None:
                continue
            roles_by_chunk.setdefault(pair[1], set()).add(role_name)
            if role_name in ORDER_SENSITIVE_SELECTION_ROLES:
                positions_by_chunk.setdefault(pair[1], {})[role_name] = position
            position += 1

    identities: list[tuple[str, dict[str, Any]]] = []
    for chunk in as_dict_list(selection.get("chunks")):
        chunk_id = str(chunk.get("chunkId") or "")
        identity = {key: value for key, value in chunk.items() if key not in _SELECTION_CHUNK_IDENTIFIER_KEYS}
        identity["roles"] = sorted(roles_by_chunk.get(chunk_id, set()))
        if positions := positions_by_chunk.get(chunk_id):
            identity["positions"] = positions
        identities.append((chunk_id, identity))
    return identities


def canonical_json(value: Any) -> str:
    """Serialize a JSON value deterministically (sorted keys, compact separators)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _normalize_documentation_selection(selection: Mapping[str, Any]) -> dict[str, Any]:
    identities = [identity for _, identity in documentation_selection_chunk_identities(selection)]
    return {
        "version": selection.get("version"),
        "chunks": sorted(identities, key=canonical_json),
    }


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
            normalized_input["chunks"],
            key=lambda x: str(x[0]) if isinstance(x, (list, tuple)) and len(x) > 0 else "",
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
    selection = normalized_input.get(DOCUMENTATION_SELECTION_INPUT_KEY)
    if isinstance(selection, Mapping):
        # Chunk UUIDs differ between sessions holding the same documentation; the
        # content and the role each chunk plays in each attempt decide reuse.
        normalized_input[DOCUMENTATION_SELECTION_INPUT_KEY] = _normalize_documentation_selection(selection)
    return normalized_input


def normalized_input_fingerprint(input_payload: dict[str, Any]) -> str:
    """Return a compact, deterministic cache identity for a job input.

    The full input remains in ``jobs.input`` for execution and diagnostics. The
    fingerprint column stores only this SHA-256 hex digest, avoiding another copy
    of large documentation corpora while preserving exact cache equality
    semantics.
    """
    normalized = normalize_input(to_jsonable(input_payload))
    return hashlib.sha256(canonical_json(normalized).encode("utf-8")).hexdigest()
