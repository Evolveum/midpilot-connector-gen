# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
import logging
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, cast
from uuid import UUID

from langchain_core.runnables.config import RunnableConfig

from ....common.enums import JobStage
from ....common.jobs import update_job_progress
from ....common.langfuse import langfuse_handler
from ..schema import ObjectClass, ObjectClassSchemaResponse

logger = logging.getLogger(__name__)


def merge_auth_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Merge auth results from multiple documents.

    Combines all auth entries from different documents and deduplicates them based on:
    - name (normalized)
    - type (normalized)

    Merges quirks from duplicate entries.
    """
    seen: Dict[tuple, Dict[str, Any]] = {}

    for result in results:
        if isinstance(result, dict) and "auth" in result:
            for auth in result["auth"]:
                name_norm = (auth.get("name") or "").strip().lower()
                type_norm = (auth.get("type") or "").strip().lower()
                key = (name_norm, type_norm)

                if key not in seen:
                    seen[key] = auth
                else:
                    # Merge quirks if present
                    quirks = auth.get("quirks", "")
                    if quirks and quirks not in (seen[key].get("quirks") or ""):
                        existing_quirks = seen[key].get("quirks") or ""
                        seen[key]["quirks"] = f"{existing_quirks}; {quirks}" if existing_quirks else quirks

    return {"auth": list(seen.values())}


def merge_relations_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Merge relations results from multiple documents.

    Combines all relations from different documents and deduplicates them based on:
    - subject
    - subjectAttribute
    - object
    - objectAttribute
    """
    # Get all relations from all results
    all_relations = []
    for result in results:
        if isinstance(result, dict) and "relations" in result:
            all_relations.extend(result["relations"])

    # Deduplicate relations based on the specified parameters
    seen = set()
    unique_relations = []

    for rel in all_relations:
        # Create a unique key based on the specified parameters
        key = (
            rel.get("subject", ""),
            rel.get("subjectAttribute", ""),
            rel.get("object", ""),
            rel.get("objectAttribute", ""),
        )

        # Only add if we haven't seen this combination before
        if key not in seen:
            seen.add(key)
            unique_relations.append(rel)

    return {"relations": unique_relations}


def merge_object_classes(
    all_object_classes: List[ObjectClass],
    class_to_chunks: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> List[ObjectClass]:
    """
    Deduplicate and merge object classes across documents.

    - Merges class metadata (superclass/abstract/embedded/description).
    - Merges relevant chunks by docUuid when provided.
    - Removes duplicates that differ only by whitespace.
    """
    by_name: Dict[str, ObjectClass] = {}

    for obj_class in all_object_classes:
        if not obj_class or not obj_class.name:
            continue
        key = obj_class.name.strip().lower()
        if key not in by_name:
            # If we have chunk information for this class, set it
            if class_to_chunks and key in class_to_chunks:
                # Remove duplicate documents (same docUuid)
                unique_chunks: List[Dict[str, Any]] = []
                seen: set[str] = set()
                for chunk in class_to_chunks[key]:
                    doc_uuid = str(chunk["docUuid"])
                    if doc_uuid not in seen:
                        seen.add(doc_uuid)
                        unique_chunks.append(chunk)
                # Sort chunks by docUuid
                obj_class.relevant_chunks = sorted(unique_chunks, key=lambda x: str(x["docUuid"]))
            by_name[key] = obj_class
            continue

        current = by_name[key]
        # Prefer non-empty superclass, keep original if new is empty
        if obj_class.superclass and not current.superclass:
            current.superclass = obj_class.superclass
        # OR booleans (any evidence of True wins)
        current.abstract = current.abstract or obj_class.abstract
        current.embedded = current.embedded or obj_class.embedded
        # Prefer longer, non-empty description
        if obj_class.description and len(obj_class.description) > len(current.description or ""):
            current.description = obj_class.description
        # Merge relevant chunks if available
        if class_to_chunks and key in class_to_chunks:
            # Convert to set of docUuids to remove duplicates
            current_doc_uuids = set(chunk["docUuid"] for chunk in (current.relevant_chunks or []))
            # Add new document UUIDs
            for chunk in class_to_chunks[key]:
                current_doc_uuids.add(chunk["docUuid"])
            # Convert back to list of dicts and sort
            current.relevant_chunks = [{"docUuid": doc_uuid} for doc_uuid in sorted(current_doc_uuids)]

    # Remove duplicates with whitespace-only differences (preferring no-space versions)
    for key in list(by_name.keys()):
        key_no_space = key.replace(" ", "")
        if key != key_no_space and key_no_space in by_name:
            by_name.pop(key)

    return list(by_name.values())


async def merge_attribute_candidates(
    *,
    object_class: str,
    per_chunk: List[Dict[str, Dict[str, Any]]],
    job_id: UUID,
    build_dedupe_chain: Callable[[], Any],
) -> Dict[str, Dict[str, Any]]:
    """
    Merge attribute candidates extracted from multiple chunks and deduplicate via LLM when needed.
    """

    await update_job_progress(
        job_id,
        stage="merging",
        message=f"Merging and deduplicating attributes for {object_class}",
    )

    candidates: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for partial in per_chunk:
        if not partial:
            continue
        for attr_name, attr_info in partial.items():
            info_copy = dict(attr_info)
            info_copy.setdefault("name", attr_name)
            candidates[attr_name].append({"info": info_copy})

    if not candidates:
        return {}

    if not any(len(v) > 1 for v in candidates.values()):
        return {name: infos[0]["info"] for name, infos in candidates.items()}

    await update_job_progress(
        job_id,
        stage=JobStage.resolving_duplicates,
        message=f"Resolving duplicate attributes for {object_class}",
    )

    dedupe_chain = build_dedupe_chain()
    payload = json.dumps(candidates, ensure_ascii=False)

    try:
        result = await dedupe_chain.ainvoke(
            {
                "object_class": object_class,
                "candidates_json": payload,
                "guaranteed_candidates_per_name": True,
            },
            config=RunnableConfig(callbacks=[langfuse_handler]),
        )

        if isinstance(result, ObjectClassSchemaResponse):
            parsed = result
        else:
            content = getattr(result, "content", None)
            parsed = (
                ObjectClassSchemaResponse.model_validate(json.loads(content))
                if content
                else ObjectClassSchemaResponse()
            )

        return {name: info.model_dump() for name, info in parsed.attributes.items()}

    except Exception as exc:
        logger.error("[Digester:Attributes] Dedupe failed: %s", exc)
        fallback: Dict[str, Dict[str, Any]] = {}
        object_class_lower = object_class.lower()
        for attr_name, attr_list in candidates.items():
            best = max(
                attr_list,
                key=lambda c: int(object_class_lower in (c["info"].get("description", "").lower())),
            )
            fallback[attr_name] = cast(Dict[str, Any], best["info"])
        return fallback
