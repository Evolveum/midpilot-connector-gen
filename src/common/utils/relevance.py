# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import copy
from collections import defaultdict
from typing import Any, Dict, List, Mapping, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.common.database.repositories.relevant_chunk_repository import RelevantChunkRepository
from src.common.utils.normalize import normalize_endpoint_key, normalize_object_class_name, normalize_url


def build_auth_entity_key(name: Any, auth_type: Any) -> str:
    return f"{str(name or '').strip().lower()}|{str(auth_type or '').strip().lower()}"


def build_endpoint_entity_key(path: Any, method: Any) -> Optional[str]:
    normalized = normalize_endpoint_key(path, method)
    if normalized is None:
        return None
    path_norm, method_norm = normalized
    return f"{method_norm} {path_norm}"


def result_key_uses_endpoint_entities(result_key: str) -> bool:
    return result_key.endswith("EndpointsOutput") or result_key == "connectivityEndpointOutput"


def unwrap_result_payload(result_dict: Dict[str, Any]) -> Dict[str, Any]:
    result = result_dict.get("result")
    return result if isinstance(result, dict) else result_dict


def build_chunk_to_doc_map(doc_items: Any) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    if not isinstance(doc_items, list):
        return mapping

    for item in doc_items:
        if not isinstance(item, dict):
            continue
        chunk_id = item.get("chunk_id") or item.get("chunkId")
        doc_id = item.get("doc_id") or item.get("docId")
        if chunk_id and doc_id:
            mapping[str(chunk_id)] = str(doc_id)
    return mapping


def build_chunk_ref_remap(
    previous_doc_items: List[Dict[str, Any]],
    current_doc_items: List[Dict[str, Any]],
) -> Dict[str, Dict[str, str]]:
    """
    Build mapping from previous chunkId -> current {docId, chunkId} using (url, content) identity.
    If current docs contain ambiguous duplicates for the same (url, content), they are ignored.
    """
    current_index: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for item in current_doc_items:
        if not isinstance(item, Mapping):
            continue
        chunk_id = item.get("chunk_id") or item.get("chunkId")
        doc_id = item.get("doc_id") or item.get("docId")
        if not chunk_id or not doc_id:
            continue
        key = (
            normalize_url(item.get("url")),
            str(item.get("content") or ""),
        )
        current_index[key].append({"chunkId": str(chunk_id), "docId": str(doc_id)})

    remap: Dict[str, Dict[str, str]] = {}
    for previous_item in previous_doc_items:
        if not isinstance(previous_item, Mapping):
            continue

        previous_chunk_id = previous_item.get("chunk_id") or previous_item.get("chunkId")
        if not previous_chunk_id:
            continue

        key = (
            normalize_url(previous_item.get("url")),
            str(previous_item.get("content") or ""),
        )
        candidates = current_index.get(key, [])
        if len(candidates) != 1:
            continue
        remap[str(previous_chunk_id)] = candidates[0]

    return remap


def remap_reused_output_relevance(
    payload: Dict[str, Any],
    *,
    chunk_ref_remap: Mapping[str, Dict[str, str]],
    top_level_doc_refs_snake_case: bool = True,
) -> Dict[str, Any]:
    """
    Remap relevance references in a reused result payload from old chunk IDs to current docs.

    Rules:
    - `relevantDocumentations` is preserved at all places.
    - Top-level `relevantDocumentations` can be serialized as snake_case (`doc_id`, `chunk_id`)
      to preserve historical digester output shape.
    - Sequence payloads are normalized into `relevant_sequences` with snake_case sequence keys.
    """

    def _remap_doc_refs(value: Any, *, snake_case: bool) -> list[Dict[str, str]]:
        if not isinstance(value, list):
            return []

        mapped: list[Dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for item in value:
            if not isinstance(item, Mapping):
                continue
            chunk_id = item.get("chunk_id") or item.get("chunkId")
            if not chunk_id:
                continue
            remapped = chunk_ref_remap.get(str(chunk_id))
            if not remapped:
                continue
            doc_id = remapped["docId"]
            target_chunk_id = remapped["chunkId"]
            key = (doc_id, target_chunk_id)
            if key in seen:
                continue
            seen.add(key)
            mapped.append(
                {"doc_id": doc_id, "chunk_id": target_chunk_id}
                if snake_case
                else {"docId": doc_id, "chunkId": target_chunk_id}
            )
        return mapped

    def _remap_sequence_refs(value: Any) -> list[Dict[str, str]]:
        if not isinstance(value, list):
            return []

        mapped: list[Dict[str, str]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for item in value:
            if not isinstance(item, Mapping):
                continue
            chunk_id = item.get("chunk_id") or item.get("chunkId")
            if not chunk_id:
                continue
            remapped = chunk_ref_remap.get(str(chunk_id))
            if not remapped:
                continue

            start_sequence = item.get("start_sequence") or item.get("startSequence")
            end_sequence = item.get("end_sequence") or item.get("endSequence")
            if not start_sequence or not end_sequence:
                continue

            doc_id = remapped["docId"]
            target_chunk_id = remapped["chunkId"]
            key = (doc_id, target_chunk_id, str(start_sequence), str(end_sequence))
            if key in seen:
                continue
            seen.add(key)
            mapped.append(
                {
                    "doc_id": doc_id,
                    "chunk_id": target_chunk_id,
                    "start_sequence": str(start_sequence),
                    "end_sequence": str(end_sequence),
                }
            )
        return mapped

    def _remap_node(node: Any, *, is_root: bool) -> Any:
        if isinstance(node, list):
            return [_remap_node(item, is_root=False) for item in node]
        if not isinstance(node, dict):
            return node

        remapped: Dict[str, Any] = {}
        for key, value in node.items():
            if key == "relevantDocumentations":
                remapped[key] = _remap_doc_refs(
                    value,
                    snake_case=is_root and top_level_doc_refs_snake_case,
                )
                continue

            if key in {"relevant_sequences", "relevantSequences"}:
                remapped["relevant_sequences"] = _remap_sequence_refs(value)
                continue

            remapped[key] = _remap_node(value, is_root=False)

        return remapped

    return _remap_node(payload, is_root=True)


def normalize_relevant_sequence(value: Any) -> Dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    start_sequence = value.get("start_sequence") or value.get("startSequence")
    end_sequence = value.get("end_sequence") or value.get("endSequence")
    if not start_sequence or not end_sequence:
        return {}
    return {
        "startSequence": str(start_sequence),
        "endSequence": str(end_sequence),
    }


def normalize_chunk_refs_for_storage(
    value: Any,
    *,
    result_key: str,
    entity_key: Optional[str] = None,
    chunk_to_doc: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []

    normalized: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue

        chunk_id = item.get("chunk_id") or item.get("chunkId")
        if not chunk_id:
            continue

        doc_id = item.get("doc_id") or item.get("docId")
        if not doc_id and chunk_to_doc:
            doc_id = chunk_to_doc.get(str(chunk_id))
        if not doc_id:
            continue

        normalized.append(
            {
                "result_key": result_key,
                "entity_key": entity_key,
                "doc_id": str(doc_id),
                "chunk_id": str(chunk_id),
                "relevant_sequence": normalize_relevant_sequence(
                    item.get("relevant_sequence") or item.get("relevantSequence")
                ),
            }
        )

    return normalized


def extract_relevant_rows_for_storage(
    result_dict: Dict[str, Any],
    *,
    result_key: str,
    chunk_to_doc: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    payload = unwrap_result_payload(result_dict)

    # For auth outputs we persist only sequence-based evidence per auth entity.
    # Top-level relevantDocumentations would create duplicate rows with empty relevant_sequence.
    if result_key != "authOutput":
        rows.extend(
            normalize_chunk_refs_for_storage(
                result_dict.get("relevantDocumentations"),
                result_key=result_key,
                chunk_to_doc=chunk_to_doc,
            )
        )

    if result_key == "objectClassesOutput":
        object_classes = payload.get("objectClasses")
        if isinstance(object_classes, list):
            for obj_class in object_classes:
                if not isinstance(obj_class, Mapping):
                    continue
                name = obj_class.get("name")
                if isinstance(name, str):
                    rows.extend(
                        normalize_chunk_refs_for_storage(
                            obj_class.get("relevantDocumentations"),
                            result_key=result_key,
                            entity_key=normalize_object_class_name(name),
                            chunk_to_doc=chunk_to_doc,
                        )
                    )

    if result_key == "authOutput":
        auth_items = payload.get("auth")
        if isinstance(auth_items, list):
            for auth_item in auth_items:
                if not isinstance(auth_item, Mapping):
                    continue
                entity_key = build_auth_entity_key(auth_item.get("name"), auth_item.get("type"))
                if entity_key == "|":
                    continue
                rows.extend(
                    _sequence_rows(
                        auth_item.get("relevant_sequences") or auth_item.get("relevantSequences"),
                        result_key=result_key,
                        entity_key=entity_key,
                        chunk_to_doc=chunk_to_doc,
                    )
                )

    if result_key.endswith("AttributesOutput"):
        attributes = payload.get("attributes")
        if isinstance(attributes, Mapping):
            for attr_name, attr_info in attributes.items():
                if not isinstance(attr_name, str) or not isinstance(attr_info, Mapping):
                    continue
                entity_key = normalize_object_class_name(attr_name)
                sequence_value = attr_info.get("relevant_sequences") or attr_info.get("relevantSequences")
                has_sequence_field = "relevant_sequences" in attr_info or "relevantSequences" in attr_info
                sequence_rows = _sequence_rows(
                    sequence_value,
                    result_key=result_key,
                    entity_key=entity_key,
                    chunk_to_doc=chunk_to_doc,
                )
                if has_sequence_field:
                    # Sequence-aware payload: persist only rows with valid sequence boundaries.
                    rows.extend(sequence_rows)
                    continue

                rows.extend(
                    normalize_chunk_refs_for_storage(
                        attr_info.get("relevantDocumentations") or attr_info.get("relevant_documentations"),
                        result_key=result_key,
                        entity_key=entity_key,
                        chunk_to_doc=chunk_to_doc,
                    )
                )

    if result_key_uses_endpoint_entities(result_key):
        endpoints = payload.get("endpoints")
        if isinstance(endpoints, list):
            for endpoint in endpoints:
                if not isinstance(endpoint, Mapping):
                    continue
                endpoint_entity_key = build_endpoint_entity_key(endpoint.get("path"), endpoint.get("method"))
                if endpoint_entity_key:
                    rows.extend(
                        normalize_chunk_refs_for_storage(
                            endpoint.get("relevantDocumentations"),
                            result_key=result_key,
                            entity_key=endpoint_entity_key,
                            chunk_to_doc=chunk_to_doc,
                        )
                    )

    return rows


def strip_relevance_from_session_payload(payload: Any, *, result_key: str) -> Any:
    if not isinstance(payload, dict):
        return payload

    cleaned = copy.deepcopy(payload)
    cleaned.pop("relevantDocumentations", None)
    cleaned.pop("relevant_chunk_indices", None)

    if result_key == "objectClassesOutput":
        object_classes = cleaned.get("objectClasses")
        if isinstance(object_classes, list):
            for obj_class in object_classes:
                if isinstance(obj_class, dict):
                    obj_class.pop("relevantDocumentations", None)

    if result_key == "authOutput":
        auth_items = cleaned.get("auth")
        if isinstance(auth_items, list):
            for auth_item in auth_items:
                if isinstance(auth_item, dict):
                    auth_item.pop("relevant_sequences", None)
                    auth_item.pop("relevantSequences", None)

    if result_key.endswith("AttributesOutput"):
        attributes = cleaned.get("attributes")
        if isinstance(attributes, dict):
            for attr_info in attributes.values():
                if isinstance(attr_info, dict):
                    attr_info.pop("relevantDocumentations", None)
                    attr_info.pop("relevant_documentations", None)
                    attr_info.pop("relevant_sequences", None)
                    attr_info.pop("relevantSequences", None)

    if result_key_uses_endpoint_entities(result_key):
        endpoints = cleaned.get("endpoints")
        if isinstance(endpoints, list):
            for endpoint in endpoints:
                if isinstance(endpoint, dict):
                    endpoint.pop("relevantDocumentations", None)

    return cleaned


async def load_relevance_map_for_result(
    db: AsyncSession,
    session_id: UUID,
    result_key: str,
) -> Dict[str, list[Dict[str, Any]]]:
    try:
        repo = RelevantChunkRepository(db)
        by_entity = await repo.get_relevant_chunks_grouped_by_entity(
            session_id=session_id,
            result_key=result_key,
        )
    except Exception:
        return {}

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


def attribute_entity_key(attribute_name: Any) -> Optional[str]:
    if not isinstance(attribute_name, str):
        return None
    normalized = normalize_object_class_name(attribute_name)
    return normalized or None


def extract_attributes_map(payload: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
    attributes = payload.get("attributes")
    if isinstance(attributes, dict):
        return attributes, True
    return payload, False


def strip_attributes_relevance(payload: Dict[str, Any]) -> Dict[str, Any]:
    stripped = dict(payload)
    attributes_map, is_wrapped = extract_attributes_map(stripped)
    if not isinstance(attributes_map, dict):
        return stripped

    cleaned_map: Dict[str, Any] = {}
    for name, info in attributes_map.items():
        if not isinstance(info, dict):
            cleaned_map[name] = info
            continue
        item = dict(info)
        item.pop("relevantDocumentations", None)
        item.pop("relevant_documentations", None)
        item.pop("relevant_sequences", None)
        item.pop("relevantSequences", None)
        cleaned_map[name] = item

    if is_wrapped:
        stripped["attributes"] = cleaned_map
        return stripped
    return cleaned_map


def extract_attribute_relevance_rows(
    payload: Dict[str, Any],
    result_key: str,
    chunk_to_doc: Optional[Dict[str, str]] = None,
) -> list[Dict[str, Any]]:
    attributes_map, _ = extract_attributes_map(payload)
    if not isinstance(attributes_map, dict):
        return []

    rows: list[Dict[str, Any]] = []
    for attribute_name, info in attributes_map.items():
        if not isinstance(info, dict):
            continue
        entity_key = attribute_entity_key(attribute_name)
        if not entity_key:
            continue
        sequence_value = info.get("relevant_sequences") or info.get("relevantSequences")
        has_sequence_field = "relevant_sequences" in info or "relevantSequences" in info
        sequence_rows = _sequence_rows(
            sequence_value,
            result_key=result_key,
            entity_key=entity_key,
            chunk_to_doc=chunk_to_doc,
        )
        if has_sequence_field:
            # Sequence-aware payload: persist only rows with valid sequence boundaries.
            rows.extend(sequence_rows)
            continue

        rows.extend(
            normalize_chunk_refs_for_storage(
                info.get("relevantDocumentations") or info.get("relevant_documentations"),
                result_key=result_key,
                entity_key=entity_key,
                chunk_to_doc=chunk_to_doc,
            )
        )
    return rows


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


def strip_endpoints_relevance(payload: Dict[str, Any]) -> Dict[str, Any]:
    return strip_relevance_from_session_payload(payload, result_key="EndpointsOutput")


def extract_endpoint_relevance_rows(payload: Dict[str, Any], result_key: str) -> list[Dict[str, Any]]:
    endpoints = payload.get("endpoints")
    if not isinstance(endpoints, list):
        return []

    rows: list[Dict[str, Any]] = []
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        entity_key = build_endpoint_entity_key(endpoint.get("path"), endpoint.get("method"))
        if entity_key:
            rows.extend(
                normalize_chunk_refs_for_storage(
                    endpoint.get("relevantDocumentations"),
                    result_key=result_key,
                    entity_key=entity_key,
                )
            )
    return rows


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
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
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
    for obj_class in object_classes:
        if not isinstance(obj_class, dict):
            continue
        item = dict(obj_class)
        class_name = item.get("name")
        item["relevantDocumentations"] = (
            relevance_map.get(normalize_object_class_name(class_name), []) if isinstance(class_name, str) else []
        )
        normalized_classes.append(item)

    hydrated["objectClasses"] = normalized_classes
    return hydrated


def extract_object_class_relevance_rows(payload: Dict[str, Any]) -> list[Dict[str, Any]]:
    object_classes = payload.get("objectClasses")
    if not isinstance(object_classes, list):
        return []

    rows: list[Dict[str, Any]] = []
    for obj_class in object_classes:
        if not isinstance(obj_class, dict):
            continue
        class_name = obj_class.get("name")
        if not isinstance(class_name, str):
            continue
        rows.extend(
            normalize_chunk_refs_for_storage(
                obj_class.get("relevantDocumentations"),
                result_key="objectClassesOutput",
                entity_key=normalize_object_class_name(class_name),
            )
        )
    return rows


def strip_object_class_relevance(payload: Dict[str, Any]) -> Dict[str, Any]:
    return strip_relevance_from_session_payload(payload, result_key="objectClassesOutput")


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
        refs = by_entity.get(build_auth_entity_key(item.get("name"), item.get("type")), [])
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


def _sequence_rows(
    value: Any,
    *,
    result_key: str,
    entity_key: str,
    chunk_to_doc: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []

    rows: List[Dict[str, Any]] = []
    for sequence in value:
        if not isinstance(sequence, Mapping):
            continue
        chunk_id = sequence.get("chunk_id") or sequence.get("chunkId")
        if not chunk_id:
            continue
        doc_id = sequence.get("doc_id") or sequence.get("docId")
        if not doc_id and chunk_to_doc:
            doc_id = chunk_to_doc.get(str(chunk_id))
        if not doc_id:
            continue
        rows.append(
            {
                "result_key": result_key,
                "entity_key": entity_key,
                "doc_id": str(doc_id),
                "chunk_id": str(chunk_id),
                "relevant_sequence": normalize_relevant_sequence(sequence),
            }
        )
    return rows


def _split_relevance_refs(refs: list[Dict[str, Any]]) -> tuple[list[Dict[str, str]], list[Dict[str, str]]]:
    relevant_docs: list[Dict[str, str]] = []
    seen_docs: set[tuple[str, str]] = set()
    relevant_sequences: list[Dict[str, str]] = []
    seen_sequences: set[tuple[str, str, str]] = set()

    for ref in refs:
        doc_id = ref.get("docId")
        chunk_id = ref.get("chunkId")
        if isinstance(doc_id, str) and isinstance(chunk_id, str):
            doc_key = (doc_id, chunk_id)
            if doc_key not in seen_docs:
                seen_docs.add(doc_key)
                relevant_docs.append({"docId": doc_id, "chunkId": chunk_id})

        sequence = ref.get("relevantSequence")
        if not isinstance(sequence, dict):
            continue
        start_sequence = sequence.get("startSequence")
        end_sequence = sequence.get("endSequence")
        if not (isinstance(chunk_id, str) and isinstance(start_sequence, str) and isinstance(end_sequence, str)):
            continue

        seq_key = (chunk_id, start_sequence, end_sequence)
        if seq_key in seen_sequences:
            continue
        seen_sequences.add(seq_key)
        relevant_sequences.append(
            {
                "chunkId": chunk_id,
                "startSequence": start_sequence,
                "endSequence": end_sequence,
            }
        )

    return relevant_docs, relevant_sequences
