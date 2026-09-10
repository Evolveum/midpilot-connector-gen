# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Relevance persistence: load and hydrate relevant-chunk rows from the database."""

from typing import Any, Dict, Mapping
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.database.repositories.relevant_chunk_repository import RelevantChunkRepository
from src.documents.normalize import normalize_object_class_name
from src.documents.relevance.transforms import (
    _split_relevance_refs,
    attribute_entity_key,
    build_endpoint_entity_key,
    extract_attributes_map,
)
from src.shared.auth import auth_entity_key
from src.shared.coerce import as_dict_list


async def load_relevance_map_for_result(
    db: AsyncSession,
    session_id: UUID,
    result_key: str,
) -> Dict[str, list[Dict[str, Any]]]:
    """Return the stored relevance references grouped by entity key.

    Database failures propagate: an empty map means the result genuinely has no
    relevance rows, and callers that want to degrade on an outage decide that
    themselves rather than being handed an indistinguishable empty result.
    """
    repo = RelevantChunkRepository(db)
    by_entity = await repo.get_relevant_chunks_grouped_by_entity(
        session_id=session_id,
        result_key=result_key,
    )

    normalized: Dict[str, list[Dict[str, Any]]] = {}
    for entity_key, refs in by_entity.items():
        normalized_refs: list[Dict[str, Any]] = []
        for ref in refs:
            doc_id = ref.get("docId") or ref.get("doc_id")
            chunk_id = ref.get("chunkId") or ref.get("chunk_id")
            if not doc_id or not chunk_id:
                continue

            item: Dict[str, Any] = {"docId": str(doc_id), "chunkId": str(chunk_id)}
            sequence = ref.get("relevantSequence")
            if isinstance(sequence, dict):
                start_sequence = sequence.get("startSequence")
                end_sequence = sequence.get("endSequence")
                if start_sequence and end_sequence:
                    item["relevantSequence"] = {
                        "startSequence": str(start_sequence),
                        "endSequence": str(end_sequence),
                    }
            normalized_refs.append(item)
        normalized[entity_key] = normalized_refs

    return normalized


async def load_object_class_relevance_map(
    db: AsyncSession,
    session_id: UUID,
) -> Dict[str, list[Dict[str, Any]]]:
    by_entity = await load_relevance_map_for_result(db, session_id, "objectClassesOutput")
    return {entity_key: refs for entity_key, refs in by_entity.items() if entity_key}


async def hydrate_attributes_with_relevance(
    db: AsyncSession,
    session_id: UUID,
    result_key: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    hydrated = dict(payload)
    attributes_map, is_wrapped = extract_attributes_map(hydrated)
    if not isinstance(attributes_map, dict):
        return hydrated

    relevance_map = await load_relevance_map_for_result(db, session_id, result_key)
    normalized_map: Dict[str, Any] = {}

    for attribute_name, info in attributes_map.items():
        if not isinstance(info, dict):
            normalized_map[attribute_name] = info
            continue

        item = dict(info)
        refs = relevance_map.get(attribute_entity_key(attribute_name) or "", [])
        relevant_docs, relevant_sequences = _split_relevance_refs(refs)
        item["relevantDocumentations"] = relevant_docs
        item["relevant_sequences"] = relevant_sequences
        item.pop("relevantSequences", None)
        normalized_map[attribute_name] = item

    if is_wrapped:
        hydrated["attributes"] = normalized_map
        return hydrated
    return normalized_map


async def hydrate_endpoints_with_relevance(
    db: AsyncSession,
    session_id: UUID,
    result_key: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    hydrated = dict(payload)
    endpoints = hydrated.get("endpoints")
    if not isinstance(endpoints, list):
        return hydrated

    relevance_map = await load_relevance_map_for_result(db, session_id, result_key)
    normalized_endpoints: list[Dict[str, Any]] = []
    for endpoint in as_dict_list(endpoints):
        item = dict(endpoint)
        refs = relevance_map.get(build_endpoint_entity_key(item.get("path"), item.get("method")) or "", [])
        relevant_docs, _ = _split_relevance_refs(refs)
        item["relevantDocumentations"] = relevant_docs
        normalized_endpoints.append(item)

    hydrated["endpoints"] = normalized_endpoints
    return hydrated


async def hydrate_object_classes_with_relevance(
    db: AsyncSession,
    session_id: UUID,
    object_classes_output: Dict[str, Any],
) -> Dict[str, Any]:
    hydrated = dict(object_classes_output)
    object_classes = hydrated.get("objectClasses")
    if not isinstance(object_classes, list):
        return hydrated

    relevance_map = await load_object_class_relevance_map(db, session_id)
    normalized_classes: list[Dict[str, Any]] = []
    for obj_class in as_dict_list(object_classes):
        item = dict(obj_class)
        class_name = item.get("name")
        item["relevantDocumentations"] = (
            relevance_map.get(normalize_object_class_name(class_name), []) if isinstance(class_name, str) else []
        )
        normalized_classes.append(item)

    hydrated["objectClasses"] = normalized_classes
    return hydrated


async def hydrate_auth_sequences_from_relevance(
    db: AsyncSession,
    session_id: UUID,
    auth_payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    auth_items = auth_payload.get("auth")
    if not isinstance(auth_items, list):
        return auth_payload

    by_entity = await load_relevance_map_for_result(db, session_id, "authOutput")
    hydrated = dict(auth_payload)
    hydrated_auth_items: list[dict[str, Any]] = []
    for auth_item in auth_items:
        if not isinstance(auth_item, Mapping):
            continue
        item = dict(auth_item)
        refs = by_entity.get(auth_entity_key(item.get("name"), item.get("type")), [])
        _, relevant_sequences = _split_relevance_refs(refs)
        if relevant_sequences:
            item["relevant_sequences"] = [
                {
                    "chunk_id": seq["chunkId"],
                    **({"start_sequence": seq["startSequence"]} if "startSequence" in seq else {}),
                    **({"end_sequence": seq["endSequence"]} if "endSequence" in seq else {}),
                }
                for seq in relevant_sequences
            ]
            item.pop("relevantSequences", None)
        hydrated_auth_items.append(item)

    hydrated["auth"] = hydrated_auth_items
    return hydrated
