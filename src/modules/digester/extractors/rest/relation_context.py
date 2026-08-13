# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Evidence context for the relation stages.

Assembling *what a stage gets to look at* is a separate concern from deciding what the
documentation says, and it is the concern that touches the database: the per-class attribute
and endpoint output, the chunk relevance recorded by object-class extraction, and the
character budget that keeps a sweep prompt from growing without bound.

Keeping it here leaves :mod:`relations` reading as a sequence of stages.
"""

import logging
from typing import Any, Dict, List, Mapping, Sequence, Tuple
from uuid import UUID

from src.config import config
from src.core.db import async_session_maker
from src.database.repositories.relevant_chunk_repository import RelevantChunkRepository
from src.documents.chunking import normalize_to_text
from src.documents.normalize import normalize_object_class_name
from src.modules.digester.entities.relation_candidates import (
    ObjectClassIndex,
    ObjectClassInfo,
    ObservedPair,
    select_attributes_map,
)
from src.modules.digester.extraction.metadata_helper import build_doc_metadata_map

logger = logging.getLogger(__name__)

LOG_SCOPE = "Digester:Relations"
"""Single log prefix for every relation module, so records from all stages group together."""

CHUNK_SEPARATOR = "\n\n----- documentation chunk -----\n\n"


def build_chunk_lookup(doc_items: List[dict]) -> Dict[str, Dict[str, Any]]:
    """Index chunk id -> content and reference, so later stages never rescan ``doc_items``.

    The case-folded body is computed once here: the per-class fallback scan below runs over
    every chunk for every class, and folding the whole corpus per class would be O(classes x
    corpus) of blocking string work on the event loop.
    """
    metadata_map = build_doc_metadata_map(doc_items)
    lookup: Dict[str, Dict[str, Any]] = {}
    for item in doc_items:
        chunk_id = item.get("chunkId")
        doc_id = item.get("docId")
        if not chunk_id:
            continue
        chunk_key = str(chunk_id)
        content = normalize_to_text(item.get("content") or "")
        lookup[chunk_key] = {
            "content": content,
            "folded": content.casefold(),
            "ref": {"doc_id": str(doc_id), "chunk_id": chunk_key} if doc_id else None,
            "metadata": metadata_map.get(chunk_key),
        }
    return lookup


def classes_for_prompt(index: ObjectClassIndex) -> List[ObjectClassInfo]:
    """Every extracted class, ordered by IGA rank, truncated to the prompt budget.

    The full list is offered - not only the confident ones - because a relation pointing at
    a low-ranked class is evidence the ranking was wrong, not evidence the relation is fake.
    """
    limit = config.digester.relation_prompt_max_object_classes
    ordered = sorted(index.all, key=lambda info: (info.confidence_rank, info.order))
    if len(ordered) > limit:
        logger.info("[%s] Object-class list truncated to %d of %d entries for prompts", LOG_SCOPE, limit, len(ordered))
        return ordered[:limit]
    return ordered


def relation_schema_output_keys(index: ObjectClassIndex) -> List[str]:
    """Return every session-data key that can affect relation schema evidence."""
    return [
        result_key
        for info in index.all
        for result_key in (
            f"{normalize_object_class_name(info.name)}AttributesOutput",
            f"{normalize_object_class_name(info.name)}EndpointsOutput",
        )
    ]


def build_relation_schema_snapshot(
    index: ObjectClassIndex,
    stored_values: Mapping[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Capture the exact attribute and endpoint outputs used by relation extraction.

    The snapshot is stored in the durable job input, making these session dependencies
    part of cache identity and ensuring the worker consumes the same values that were
    fingerprinted when the job was scheduled.
    """
    attributes_by_class: Dict[str, Any] = {}
    endpoints_by_class: Dict[str, Any] = {}

    for info in index.all:
        key = normalize_object_class_name(info.name)
        attributes = stored_values.get(f"{key}AttributesOutput")
        if attributes:
            attributes_by_class[key] = attributes
        endpoints = stored_values.get(f"{key}EndpointsOutput")
        if endpoints:
            endpoints_by_class[key] = endpoints

    return {
        "attributesByClass": attributes_by_class,
        "endpointsByClass": endpoints_by_class,
    }


def unpack_relation_schema_snapshot(
    snapshot: Mapping[str, Any],
    index: ObjectClassIndex,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Validate and unpack a relation schema snapshot from durable job input."""
    attributes_by_class = snapshot.get("attributesByClass")
    endpoints_by_class = snapshot.get("endpointsByClass")
    if not isinstance(attributes_by_class, Mapping) or not isinstance(endpoints_by_class, Mapping):
        raise TypeError("Relation schema snapshot must contain attribute and endpoint mappings")

    missing_attributes = [
        info.name for info in index.all if normalize_object_class_name(info.name) not in attributes_by_class
    ]
    if missing_attributes:
        logger.info(
            "[%s] No extracted attributes for %d class(es); relation seeding falls back to documentation only: %s",
            LOG_SCOPE,
            len(missing_attributes),
            ", ".join(missing_attributes[:10]),
        )
    return dict(attributes_by_class), dict(endpoints_by_class)


async def load_class_chunk_ids(
    session_id: UUID,
    index: ObjectClassIndex,
    chunk_lookup: Dict[str, Dict[str, Any]],
) -> Dict[str, List[str]]:
    """
    Map each class to the chunks that describe it.

    Primary source is the relevance recorded by object-class extraction. Classes without
    recorded relevance fall back to a name scan over the chunk text, so a class never loses
    its sweep just because relevance was not persisted.
    """
    async with async_session_maker() as db:
        grouped = await RelevantChunkRepository(db).get_relevant_chunks_grouped_by_entity(
            session_id=session_id,
            result_key="objectClassesOutput",
        )

    class_chunk_ids: Dict[str, List[str]] = {}
    for info in index.all:
        key = normalize_object_class_name(info.name)
        chunk_ids = [
            str(chunk.get("chunkId") or chunk.get("chunk_id") or "")
            for chunk in grouped.get(key, [])
            if chunk.get("chunkId") or chunk.get("chunk_id")
        ]
        known = [chunk_id for chunk_id in chunk_ids if chunk_id in chunk_lookup]
        if not known:
            known = _chunks_mentioning(info.name, chunk_lookup)
        if known:
            class_chunk_ids[key] = known
    return class_chunk_ids


def _chunks_mentioning(class_name: str, chunk_lookup: Dict[str, Dict[str, Any]]) -> List[str]:
    """Chunks whose text mentions a class name, used only when no relevance was recorded."""
    needle = class_name.strip().casefold()
    if not needle:
        return []
    return [chunk_id for chunk_id, chunk in chunk_lookup.items() if needle in chunk["folded"]]


def assemble_documentation(chunk_ids: Sequence[str], chunk_lookup: Dict[str, Dict[str, Any]]) -> Tuple[str, int]:
    """Concatenate chunk text up to the configured budget; returns the text and skipped count."""
    budget = config.digester.relation_context_max_chars
    parts: List[str] = []
    used = 0
    skipped = 0
    for chunk_id in chunk_ids:
        chunk = chunk_lookup.get(chunk_id)
        if chunk is None:
            continue
        content = chunk["content"]
        if used + len(content) > budget and parts:
            skipped += 1
            continue
        parts.append(content)
        used += len(content)
    return CHUNK_SEPARATOR.join(parts), skipped


def chunk_refs(chunk_ids: Sequence[str], chunk_lookup: Dict[str, Dict[str, Any]]) -> List[Dict[str, str]]:
    """Deduplicated ``{doc_id, chunk_id}`` references for the given chunks."""
    refs: List[Dict[str, str]] = []
    for chunk_id in chunk_ids:
        chunk = chunk_lookup.get(chunk_id)
        if chunk and chunk["ref"] and chunk["ref"] not in refs:
            refs.append(chunk["ref"])
    return refs


def pair_chunk_ids(pair: ObservedPair, class_chunk_ids: Dict[str, List[str]]) -> List[str]:
    """Chunks where both classes appear, falling back to the union when they never co-occur."""
    left = class_chunk_ids.get(normalize_object_class_name(pair.class_a), [])
    right = class_chunk_ids.get(normalize_object_class_name(pair.class_b), [])
    right_set = set(right)
    shared = [chunk_id for chunk_id in left if chunk_id in right_set]
    if shared:
        return shared
    left_set = set(left)
    return list(left) + [chunk_id for chunk_id in right if chunk_id not in left_set]


def known_attributes_for(class_names: Sequence[str], attributes_by_class: Dict[str, Any]) -> Dict[str, List[str]]:
    """Attribute names extracted for the given classes, for the prompt and the grounding check."""
    known: Dict[str, List[str]] = {}
    for class_name in dict.fromkeys(class_names):
        payload = attributes_by_class.get(normalize_object_class_name(class_name))
        if payload is None:
            continue
        known[class_name] = sorted(str(name) for name in select_attributes_map(payload))
    return known
