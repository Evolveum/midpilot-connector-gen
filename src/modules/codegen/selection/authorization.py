# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

# TODO refactor this part

from typing import Any, Dict, List, Mapping, Optional, TypeAlias

AuthPayload: TypeAlias = Mapping[str, Any]
PreferredAuthorizations: TypeAlias = Optional[List[Dict[str, Any]]]


def _auth_key(auth_item: Mapping[str, Any]) -> tuple[str, str]:
    name = str(auth_item.get("name") or "").strip().lower()
    auth_type = str(auth_item.get("type") or "").strip().lower()
    return name, auth_type


def _auth_items_from_payload(auth_payload: AuthPayload) -> List[Mapping[str, Any]]:
    raw_auth = auth_payload.get("auth")
    if not isinstance(raw_auth, list):
        return []
    return [item for item in raw_auth if isinstance(item, Mapping)]


def enrich_preferred_authorizations(
    auth_payload: AuthPayload,
    preferred_authorizations: PreferredAuthorizations,
) -> PreferredAuthorizations:
    if not preferred_authorizations:
        return preferred_authorizations

    auth_items = _auth_items_from_payload(auth_payload)
    if not auth_items:
        return preferred_authorizations

    enriched: List[Dict[str, Any]] = []
    for preferred in preferred_authorizations:
        item = {key: value for key, value in preferred.items() if value is not None}
        preferred_name = str(item.get("name") or "").strip().lower()
        preferred_type = str(item.get("type") or "").strip().lower()

        match = next(
            (
                auth_item
                for auth_item in auth_items
                if _auth_key(auth_item) == (preferred_name, preferred_type)
                or (not preferred_type and _auth_key(auth_item)[0] == preferred_name)
            ),
            None,
        )

        if match is not None:
            if not item.get("type") and match.get("type"):
                item["type"] = str(match["type"])
            if not item.get("quirks") and match.get("quirks"):
                item["quirks"] = str(match["quirks"])

        enriched.append(item)

    return enriched


def _selected_auth_chunk_ids(auth_payload: AuthPayload, preferred_authorizations: PreferredAuthorizations) -> set[str]:
    if not preferred_authorizations:
        return set()

    preferred_keys = {_auth_key(auth) for auth in preferred_authorizations if auth.get("name")}
    preferred_names = {name for name, auth_type in preferred_keys if name and not auth_type}
    chunk_ids: set[str] = set()

    for auth_item in _auth_items_from_payload(auth_payload):
        key = _auth_key(auth_item)
        if key not in preferred_keys and key[0] not in preferred_names:
            continue

        sequences = auth_item.get("relevant_sequences") or auth_item.get("relevantSequences") or []
        if not isinstance(sequences, list):
            continue

        for sequence in sequences:
            if not isinstance(sequence, Mapping):
                continue
            chunk_id = sequence.get("chunk_id") or sequence.get("chunkId")
            if chunk_id:
                chunk_ids.add(str(chunk_id))

    return chunk_ids


def _normalize_chunk_refs(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []

    normalized: List[Dict[str, Any]] = []
    seen_chunk_ids: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            continue
        chunk_id = item.get("chunk_id") or item.get("chunkId")
        if not chunk_id:
            continue
        chunk_id_str = str(chunk_id)
        if chunk_id_str in seen_chunk_ids:
            continue
        seen_chunk_ids.add(chunk_id_str)

        chunk_ref: Dict[str, Any] = {"chunk_id": chunk_id_str}
        doc_id = item.get("doc_id") or item.get("docId")
        if doc_id:
            chunk_ref["doc_id"] = str(doc_id)
        normalized.append(chunk_ref)

    return normalized


def select_authorization_chunk_refs(
    relevant_documentations: Any,
    auth_payload: AuthPayload,
    preferred_authorizations: PreferredAuthorizations,
) -> List[Dict[str, Any]]:
    if not isinstance(relevant_documentations, Mapping):
        return []

    auth_pairs = _normalize_chunk_refs(relevant_documentations.get("authOutput"))
    if not auth_pairs:
        return []

    selected_chunk_ids = _selected_auth_chunk_ids(auth_payload, preferred_authorizations)
    if not selected_chunk_ids:
        return auth_pairs

    selected_pairs = [
        pair for pair in auth_pairs if str(pair.get("chunk_id") or pair.get("chunkId") or "") in selected_chunk_ids
    ]
    return selected_pairs or auth_pairs
