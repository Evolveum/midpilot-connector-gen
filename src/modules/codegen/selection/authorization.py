# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any, Dict, List, Mapping, Optional

from src.common.utils.coerce import as_list, as_mapping
from src.modules.codegen.schema import AuthPayload, PreferredAuthorizations
from src.modules.digester.enums import auth_type_match_key, normalize_auth_type_value

ANALYSIS_SUPPORT_FIELD = "analysisSupport"
ANALYSIS_SUPPORT_SUPPORTED = "supported"
ANALYSIS_SUPPORT_UNSUPPORTED = "unsupported"
UNSUPPORTED_AUTH_QUIRKS = (
    "Selected in midPoint, but this authentication method was not identified in the analyzed application "
    "documentation. No application-specific authorization customization can be generated."
)


def _auth_key(auth_item: Mapping[str, Any]) -> tuple[str, str]:
    name = str(auth_item.get("name") or "").strip().lower()
    auth_type = auth_type_match_key(auth_item.get("type"))
    return name, auth_type


def _normalize_preferred_authorization(preferred: Mapping[str, Any]) -> Dict[str, Any]:
    item = {key: value for key, value in preferred.items() if value is not None}
    normalized_type = normalize_auth_type_value(item.get("type"), preserve_unknown=True)
    if normalized_type:
        item["type"] = normalized_type
    else:
        item.pop("type", None)
    return item


def _auth_items_from_payload(auth_payload: AuthPayload) -> List[Mapping[str, Any]]:
    return [item for item in as_list(auth_payload.get("auth")) if isinstance(item, Mapping)]


def _find_matching_auth_item(
    auth_items: List[Mapping[str, Any]],
    preferred: Mapping[str, Any],
) -> Optional[Mapping[str, Any]]:
    preferred_name = str(preferred.get("name") or "").strip().lower()
    preferred_type = auth_type_match_key(preferred.get("type"))
    if not preferred_name:
        return None

    return next(
        (
            auth_item
            for auth_item in auth_items
            if _auth_key(auth_item) == (preferred_name, preferred_type)
            or (not preferred_type and _auth_key(auth_item)[0] == preferred_name)
        ),
        None,
    )


def enrich_preferred_authorizations(
    auth_payload: AuthPayload,
    preferred_authorizations: PreferredAuthorizations,
) -> PreferredAuthorizations:
    if not preferred_authorizations:
        return preferred_authorizations

    auth_items = _auth_items_from_payload(auth_payload)
    if not auth_items:
        return [_normalize_preferred_authorization(preferred) for preferred in preferred_authorizations]

    enriched: List[Dict[str, Any]] = []
    for preferred in preferred_authorizations:
        item = _normalize_preferred_authorization(preferred)
        match = _find_matching_auth_item(auth_items, item)

        if match is not None:
            if not item.get("type") and match.get("type"):
                item["type"] = str(match["type"])
            if not item.get("quirks") and match.get("quirks"):
                item["quirks"] = str(match["quirks"])

        enriched.append(item)

    return enriched


def prepare_preferred_authorizations_for_generation(
    auth_payload: AuthPayload,
    preferred_authorizations: PreferredAuthorizations,
) -> PreferredAuthorizations:
    enriched = enrich_preferred_authorizations(auth_payload, preferred_authorizations)
    if not enriched:
        return enriched

    auth_items = _auth_items_from_payload(auth_payload)
    prepared: List[Dict[str, Any]] = []
    for preferred in enriched:
        item = _normalize_preferred_authorization(preferred)
        if item.get("quirks"):
            item["quirks"] = str(item["quirks"]).strip()

        match = _find_matching_auth_item(auth_items, item)
        if match is None:
            item[ANALYSIS_SUPPORT_FIELD] = ANALYSIS_SUPPORT_UNSUPPORTED
            if not item.get("quirks"):
                item["quirks"] = UNSUPPORTED_AUTH_QUIRKS
        else:
            item[ANALYSIS_SUPPORT_FIELD] = ANALYSIS_SUPPORT_SUPPORTED

        prepared.append(item)

    return prepared


def is_single_other_authorization(preferred_authorizations: PreferredAuthorizations) -> bool:
    if not preferred_authorizations or len(preferred_authorizations) != 1:
        return False

    authorization = _normalize_preferred_authorization(preferred_authorizations[0])
    name = str(authorization.get("name") or "").strip().lower()
    return name == "other" and auth_type_match_key(authorization.get("type")) == "other"


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


def has_matching_preferred_authorization(
    auth_payload: AuthPayload,
    preferred_authorizations: PreferredAuthorizations,
) -> bool:
    if not preferred_authorizations:
        return False

    auth_items = _auth_items_from_payload(auth_payload)
    return any(_find_matching_auth_item(auth_items, preferred) is not None for preferred in preferred_authorizations)


def _normalize_chunk_refs(value: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    seen_chunk_ids: set[str] = set()
    for item in as_list(value):
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
    relevant_documentations = as_mapping(relevant_documentations)

    auth_pairs = _normalize_chunk_refs(relevant_documentations.get("authOutput"))
    if not auth_pairs:
        return []

    selected_chunk_ids = _selected_auth_chunk_ids(auth_payload, preferred_authorizations)
    if not selected_chunk_ids:
        return auth_pairs if has_matching_preferred_authorization(auth_payload, preferred_authorizations) else []

    selected_pairs = [
        pair for pair in auth_pairs if str(pair.get("chunk_id") or pair.get("chunkId") or "") in selected_chunk_ids
    ]
    return selected_pairs or auth_pairs
