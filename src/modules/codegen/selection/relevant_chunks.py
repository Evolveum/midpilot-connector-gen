# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Relevant-chunk selection for codegen.

Loads and normalizes the relevant documentation chunk references stored per
result key (attributes/endpoints/auth/object classes) into the ordered reference
lists consumed by the Groovy generators. Pure selection logic — no generation.
"""

import logging
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from uuid import UUID

from src.core.db import async_session_maker
from src.database.repositories.relevant_chunk_repository import RelevantChunkRepository
from src.documents.normalize import normalize_object_class_name
from src.modules.codegen.schema import AuthPayload
from src.modules.codegen.selection.authorization import (
    has_matching_preferred_authorization,
    select_authorization_chunk_refs,
)
from src.shared.normalize import normalize_chunk_pair

logger = logging.getLogger(__name__)


def _collect_pairs(val: Any) -> List[Tuple[int, Optional[str]]]:
    """
    Normalize relevant chunk references to ordered (index, chunk_id) tuples.
    """
    out: List[Tuple[int, Optional[str]]] = []
    if not val:
        return out
    if isinstance(val, list) and val:
        first = val[0]
        if isinstance(first, dict):
            for item in val:
                if not isinstance(item, dict):
                    continue
                chunk_id = item.get("chunk_id") or item.get("chunkId")
                if isinstance(chunk_id, str):
                    raw_sequence = item.get("relevant_sequence") or item.get("relevantSequence")
                    if raw_sequence is None:
                        sequence = len(out)
                    else:
                        try:
                            sequence = int(raw_sequence)
                        except Exception:
                            sequence = len(out)
                    out.append((sequence, chunk_id))
        else:
            for idx in val:
                if isinstance(idx, int):
                    out.append((idx, None))
    return out


def _merge_unique_pairs(*seqs: Iterable[Tuple[int, Optional[str]]]) -> List[Tuple[int, Optional[str]]]:
    """
    Merge multiple (idx, uuid) sequences preserving unique chunk IDs.

    When a chunk_id is present, deduplicate by chunk_id only so the same
    documentation chunk is not processed multiple times if it was selected
    from both attributes and endpoints. For legacy index-only entries
    (chunk_id is None), preserve uniqueness by the full pair.
    """
    merged: List[Tuple[int, Optional[str]]] = []
    seen_pairs: set[Tuple[int, Optional[str]]] = set()
    seen_chunk_ids: set[str] = set()
    for seq in seqs:
        for idx, chunk_id in seq:
            if isinstance(chunk_id, str):
                if chunk_id in seen_chunk_ids:
                    continue
                seen_chunk_ids.add(chunk_id)
                merged.append((idx, chunk_id))
                continue

            pair = (idx, chunk_id)
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                merged.append(pair)
    return merged


def _select_chunk_refs(*ref_groups: Sequence[Any]) -> List[Dict[str, Any]]:
    """
    Order, deduplicate and re-attach the document id of relevant chunk references.

    Every collector below reads a different set of result keys and then needs the
    same thing from them: the referenced chunks in relevance order, once each,
    carrying the ``doc_id`` the reference was stored with.
    """
    groups = [[ref for ref in group if isinstance(ref, Mapping)] for group in ref_groups]
    merged_pairs = _merge_unique_pairs(*(_collect_pairs(group) for group in groups))

    chunk_to_doc: Dict[str, str] = {}
    for group in groups:
        for ref in group:
            pair = normalize_chunk_pair(ref)
            if pair is not None:
                chunk_to_doc.setdefault(pair[1], pair[0])

    selected: List[Dict[str, Any]] = []
    for _, chunk_id in merged_pairs:
        if not chunk_id:
            continue
        selected_ref: Dict[str, Any] = {"chunk_id": chunk_id}
        if chunk_id in chunk_to_doc:
            selected_ref["doc_id"] = chunk_to_doc[chunk_id]
        selected.append(selected_ref)
    return selected


async def _collect_relation_object_class_pairs(
    session_id: UUID,
    object_class_names: Sequence[str],
) -> List[Dict[str, Any]]:
    """
    Select object-class documentation chunks for the classes a relation is built from.

    Which classes those are is decided by the caller: besides the subject and the object it
    can include the class carrying the association, whose documentation holds the endpoints
    implementing the link and appears in neither end's own documentation.
    """
    if not object_class_names:
        return []

    async with async_session_maker() as db:
        repo = RelevantChunkRepository(db)
        chunk_map = await repo.get_relevant_chunks_grouped_by_entity(
            session_id=session_id,
            result_key="objectClassesOutput",
        )

    ref_groups: List[Sequence[Any]] = []
    for class_name in object_class_names:
        relevant_refs = chunk_map.get(normalize_object_class_name(class_name), [])
        if not relevant_refs:
            logger.warning("[Codegen:Relation] No relevant chunks found for object class %s", class_name)
            continue
        ref_groups.append(relevant_refs)

    return _select_chunk_refs(*ref_groups)


async def _collect_relevant_chunks(
    session_id: UUID, object_class: str, operation_name: str
) -> Optional[List[Dict[str, Any]]]:
    """
    Collect relevant chunk references from the session for a given object class.

    Args:
        session_id: Session UUID
        object_class: Object class name
        operation_name: Operation name for logging (e.g., "Search", "Create")

    Returns:
        Ordered relevant chunk references, or ``None`` when no selection exists.
    """
    key_endpoints = f"{object_class}EndpointsOutput"
    key_attributes = f"{object_class}AttributesOutput"
    async with async_session_maker() as db:
        repo = RelevantChunkRepository(db)
        relevant_map = await repo.get_relevant_chunks_map(session_id, result_keys=[key_endpoints, key_attributes])

    endpoint_refs = relevant_map.get(key_endpoints, [])
    attribute_refs = relevant_map.get(key_attributes, [])
    relevant_pairs = _select_chunk_refs(endpoint_refs, attribute_refs)

    if not relevant_pairs:
        return None

    logger.info(
        "[Codegen:%s] Relevant chunks for endpoints=%d, for attributes=%d, merged=%d for %s",
        operation_name,
        len(endpoint_refs),
        len(attribute_refs),
        len(relevant_pairs),
        object_class,
    )

    return relevant_pairs


async def _collect_authorization_relevant_chunks(
    session_id: UUID,
    auth_payload: AuthPayload,
    preferred_authorizations: Optional[List[Dict[str, Any]]],
) -> Optional[List[Dict[str, Any]]]:
    async with async_session_maker() as db:
        repo = RelevantChunkRepository(db)
        relevant_map = await repo.get_relevant_chunks_map(session_id, result_keys=["authOutput"])

    auth_pairs = select_authorization_chunk_refs(relevant_map, auth_payload, preferred_authorizations)
    if not auth_pairs:
        if preferred_authorizations and not has_matching_preferred_authorization(
            auth_payload, preferred_authorizations
        ):
            logger.info("[Codegen:Authorization] No selected authorization was identified in analyzed auth output")
            return []
        return None

    logger.info(
        "[Codegen:Authorization] Relevant auth chunks selected=%d preferred=%s",
        len(auth_pairs),
        bool(preferred_authorizations),
    )
    return auth_pairs


async def collect_connector_relevant_chunks(
    session_id: UUID,
    object_classes: Sequence[str],
) -> List[Dict[str, Any]]:
    """
    Collect every relevant chunk reference for the requested object classes.

    Each class contributes its ``EndpointsOutput`` and ``AttributesOutput``
    references plus only its own entity from ``objectClassesOutput``. The fix
    does not rank or limit these references; it only removes duplicate chunk IDs.
    """
    normalized_object_classes = list(
        dict.fromkeys(
            normalized for object_class in object_classes if (normalized := normalize_object_class_name(object_class))
        )
    )
    if not normalized_object_classes:
        return []

    result_keys: List[str] = []
    for object_class in normalized_object_classes:
        result_keys.append(f"{object_class}EndpointsOutput")
        result_keys.append(f"{object_class}AttributesOutput")

    async with async_session_maker() as db:
        repo = RelevantChunkRepository(db)
        relevant_map = await repo.get_relevant_chunks_map(session_id, result_keys=result_keys)
        object_class_refs: List[Dict[str, Any]] = []
        for object_class in normalized_object_classes:
            refs = await repo.get_relevant_chunks(
                session_id=session_id,
                result_key="objectClassesOutput",
                entity_key=object_class,
            )
            object_class_refs.extend(ref for ref in refs if isinstance(ref, dict))

    operation_refs: List[Dict[str, Any]] = []
    for key in result_keys:
        refs = relevant_map.get(key, [])
        if isinstance(refs, list):
            operation_refs.extend(ref for ref in refs if isinstance(ref, dict))

    selected = _select_chunk_refs(operation_refs, object_class_refs)

    logger.info(
        "[Codegen:Fix] Selected %d unique relevant chunk(s) for %d object class(es) "
        "from %d endpoint/attribute and %d object-class reference(s)",
        len(selected),
        len(normalized_object_classes),
        len(operation_refs),
        len(object_class_refs),
    )
    return selected
