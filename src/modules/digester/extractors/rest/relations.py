# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

from src.common.chunks import normalize_to_text
from src.common.jobs import append_job_error, update_job_progress
from src.common.langfuse import langfuse_handler
from src.common.llm import get_default_llm, make_basic_chain
from src.common.utils.normalize import normalize_object_class_name
from src.modules.digester.enums import ConfidenceLevel
from src.modules.digester.prompts.rest.relations_prompts import (
    get_relations_system_prompt,
    get_relations_user_prompt,
)
from src.modules.digester.schema import FinalObjectClass, ObjectClassesResponse, RelationRecord, RelationsResponse
from src.modules.digester.utils.metadata_helper import extract_summary_and_tags
from src.modules.digester.utils.relations import deduplicate_semantic_relations

logger = logging.getLogger(__name__)

CONFIDENCE_PRIORITY: Dict[ConfidenceLevel, int] = {
    ConfidenceLevel.HIGH: 0,
    ConfidenceLevel.MEDIUM: 1,
    ConfidenceLevel.LOW: 2,
}
MISSING_CLASS_PRIORITY = len(CONFIDENCE_PRIORITY)


def _extract_relevant_object_classes(relevant_object_classes: Any) -> List[FinalObjectClass]:
    """
    Parse object classes from the canonical digester payload shape.
    Expected input: {"objectClasses": [...]} (camelCase aliases supported by schema model).
    """
    try:
        parsed = ObjectClassesResponse.model_validate(relevant_object_classes)
        logger.debug(
            "[Digester:Relations] Successfully extracted %d relevant object classes",
            len(parsed.object_classes),
        )
        return parsed.object_classes

    except Exception as exc:
        logger.error("[Digester:Relations] Failed to parse object classes payload: %s", exc)
        return []


def _extract_relevant_names(relevant_object_classes: Any) -> List[Tuple[str, str]]:
    """
    Extract object class names and descriptions from the object classes payload.
    Returns a list of tuples (name, description).
    """
    relevant_items = _extract_relevant_object_classes(relevant_object_classes)
    return [(item.name, item.description) for item in relevant_items]


def _confidence_order_key(confidence: ConfidenceLevel) -> int:
    return CONFIDENCE_PRIORITY[confidence]


def _build_object_class_priority_map(relevant_object_classes: Any) -> Dict[str, Tuple[int, int, int]]:
    """
    Build deterministic object-class priority map:
    1) confidence (high -> medium -> low)
    2) relative order within the same confidence bucket
    3) original global order as a stable tie-breaker
    """
    relevant_items = _extract_relevant_object_classes(relevant_object_classes)
    priority_map: Dict[str, Tuple[int, int, int]] = {}
    bucket_positions: Dict[int, int] = {}

    for global_position, item in enumerate(relevant_items):
        class_name = item.name.strip()
        if not class_name:
            continue

        confidence_rank = _confidence_order_key(item.confidence)
        bucket_position = bucket_positions.get(confidence_rank, 0)
        bucket_positions[confidence_rank] = bucket_position + 1

        class_key = normalize_object_class_name(class_name)
        candidate = (confidence_rank, bucket_position, global_position)
        existing = priority_map.get(class_key)
        if existing is None or candidate < existing:
            priority_map[class_key] = candidate

    return priority_map


def _sort_relations_by_iga_priority(
    relations: List[RelationRecord],
    relevant_object_classes: Any,
) -> List[RelationRecord]:
    """
    Sort relations by object-class importance with deterministic fallback.
    Subject class priority is primary because relation semantics are anchored to subject attributes.
    """
    if len(relations) <= 1:
        return list(relations)

    object_class_priority = _build_object_class_priority_map(relevant_object_classes)
    if not object_class_priority:
        return sorted(
            relations,
            key=lambda rel: (
                normalize_object_class_name(rel.subject),
                normalize_object_class_name(rel.subject_attribute or ""),
                normalize_object_class_name(rel.object),
                normalize_object_class_name(rel.object_attribute or ""),
            ),
        )

    missing_class_bucket_position = len(object_class_priority) + 1
    missing_class_priority = (MISSING_CLASS_PRIORITY, missing_class_bucket_position, missing_class_bucket_position)

    def _sort_key(relation: RelationRecord) -> Tuple[int, int, int, int, int, int, str, str, str]:
        subject_priority = object_class_priority.get(
            normalize_object_class_name(relation.subject),
            missing_class_priority,
        )
        object_priority = object_class_priority.get(
            normalize_object_class_name(relation.object),
            missing_class_priority,
        )
        return (
            subject_priority[0],
            subject_priority[1],
            subject_priority[2],
            object_priority[0],
            object_priority[1],
            object_priority[2],
            normalize_object_class_name(relation.subject_attribute or ""),
            normalize_object_class_name(relation.object),
            normalize_object_class_name(relation.object_attribute or ""),
        )

    return sorted(relations, key=_sort_key)


def sort_relation_dicts_by_iga_priority(
    relations: List[Any],
    relevant_object_classes: Any,
) -> List[Dict[str, Any]]:
    """
    Parse, semantically deduplicate, sort, and serialize relation payload items in one place.
    Invalid relation records are skipped to preserve robust merge behavior.
    """
    parsed_relations: List[RelationRecord] = []
    for relation in relations:
        try:
            parsed_relations.append(RelationRecord.model_validate(relation))
        except Exception:
            logger.debug("[Digester:Relations] Skipping invalid relation during final merge sort: %r", relation)

    deduplicated_relations = deduplicate_semantic_relations(parsed_relations)
    sorted_relations = _sort_relations_by_iga_priority(deduplicated_relations, relevant_object_classes)
    return [relation.model_dump(by_alias=True) for relation in sorted_relations]


def _parse_relations_result(
    result: Any,
    job_id: UUID,
    idx: Optional[int] = None,
    total_chunks: Optional[int] = None,
    chunk_id: Optional[UUID] = None,
) -> List[RelationRecord]:
    """
    Parse LLM result into RelationRecord list.
    Handles various result formats from structured output.
    """
    try:
        # Handle RelationsResponse directly
        if isinstance(result, RelationsResponse):
            logger.debug("[Digester:Relations] Found %d relations in RelationsResponse", len(result.relations))
            return result.relations

        # Handle dict format
        if isinstance(result, dict):
            if "relations" in result:
                relations_data = result["relations"]
                logger.debug("[Digester:Relations] Parsing %d relations from dict format", len(relations_data))
                return [RelationRecord.model_validate(rel) for rel in relations_data]
            else:
                # Try to parse as single RelationsResponse
                logger.debug("[Digester:Relations] Attempting to parse dict as RelationsResponse")
                parsed = RelationsResponse.model_validate(result)
                return parsed.relations

        # Handle string content (JSON)
        content = getattr(result, "content", None)
        if isinstance(content, str) and content.strip():
            data = json.loads(content)
            if "relations" in data:
                logger.debug("[Digester:Relations] Found %d relations in JSON content", len(data["relations"]))
                return [RelationRecord.model_validate(rel) for rel in data["relations"]]

        logger.warning("[Digester:Relations] Could not parse result format")
        return []

    except Exception as exc:
        if job_id is not None and idx is not None:
            try:
                total = total_chunks or 0
                prefix = "[Digester:Relations] "
                error_message = f"{prefix}Failed to parse chunk {idx + 1}/{total if total else '?'}: {exc}"
                if chunk_id:
                    error_message = f"{error_message} (chunk_id: {chunk_id})"
                logger.exception(error_message)
                append_job_error(job_id, error_message)
            except Exception:
                pass
        else:
            logger.exception("[Digester:Relations] Failed to parse relations result")
        return []


async def _extract_from_chunk(
    chain,
    idx: int,
    chunk: str,
    job_id: UUID,
    total_chunks: Optional[int] = None,
    chunk_id: Optional[UUID] = None,
    chunk_metadata: Optional[Dict[str, Any]] = None,
) -> List[RelationRecord]:
    try:
        summary, tags = extract_summary_and_tags(chunk_metadata)

        result = await chain.ainvoke(
            {"chunk": chunk, "summary": summary, "tags": tags}, config={"callbacks": [langfuse_handler]}
        )
        return _parse_relations_result(
            result,
            job_id=job_id,
            idx=idx,
            total_chunks=total_chunks,
            chunk_id=chunk_id,
        )
    except Exception as exc:
        total = total_chunks or 0
        error_message = f"[Digester:Relations] Failed to process chunk {idx + 1}/{total if total else '?'}: {exc}"
        if chunk_id:
            error_message = f"{error_message} (chunk_id: {chunk_id})"
        logger.exception(error_message)
        append_job_error(job_id, error_message)
        return []


async def extract_relations(
    schema: str,
    relevant_object_classes: Any,
    job_id: UUID,
    chunk_id: Optional[UUID] = None,
    chunk_metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[RelationsResponse, bool]:
    """
    Extract relationships between object classes from an OpenAPI/Swagger specification.

    This function analyzes the provided OpenAPI spec to identify relationships where one object class
    contains properties that reference another object class (foreign keys, IDs, $refs, etc.).

    Args:
        schema: OpenAPI/Swagger specification as string (YAML or JSON format).
        relevant_object_classes: Output from /getObjectClass endpoint. Can be JSON string, dict, list,
                              or ObjectClassesResponse.

    Returns:
        Tuple containing:
        - RelationsResponse: Contains list of discovered relationships with normalized class names,
                          subject/object attributes, and relationship metadata.
        - Boolean indicating if relevant relation data was found

    Note:
        - Class names are used exactly as provided without any normalization
        - Only relationships with strong evidence in the spec chunks are included
        - Deduplication is performed on (subject, subjectAttribute, object) tuples
    """
    logger.info("[Digester:Relations] LLM call for chunk %s", chunk_id)
    relevant_items = _extract_relevant_names(relevant_object_classes)
    if not relevant_items:
        logger.warning("[Digester:Relations] No relevant object classes; returning empty.")
        return RelationsResponse(relations=[]), False

    # Extract names and descriptions
    relevant_names = [name for name, _ in relevant_items]
    relevant_descriptions = [desc for _, desc in relevant_items]

    # Format for the prompt
    relevant_list_with_descriptions = "\n".join(
        f"- {name}: {desc}" if desc.strip() else f"- {name}" for name, desc in relevant_items
    )

    # Normalize text (input is already pre-chunked in DB)
    text = normalize_to_text(schema)

    if not text or not text.strip():
        logger.warning("[Digester:Relations] Empty schema provided; returning empty.")
        return RelationsResponse(relations=[]), False

    # Progress: start processing
    await update_job_progress(
        job_id,
        stage="processing_chunks",
        message="Processing chunk and extracting relations",
    )

    parser: PydanticOutputParser[RelationsResponse] = PydanticOutputParser(pydantic_object=RelationsResponse)

    llm = get_default_llm()

    prompt = ChatPromptTemplate.from_messages(
        [("system", get_relations_system_prompt + "\n\n{format_instructions}"), ("human", get_relations_user_prompt)]
    ).partial(
        relevant_list=relevant_names,
        relevant_descriptions=relevant_descriptions,
        relevant_list_with_descriptions=relevant_list_with_descriptions,
        format_instructions=parser.get_format_instructions(),
    )

    chain = make_basic_chain(prompt, llm, parser)

    # Process the single pre-chunked input (no need for asyncio.gather with just one item)
    chunk_results = [
        await _extract_from_chunk(
            chain,
            0,
            text,
            job_id,
            total_chunks=1,
            chunk_id=chunk_id,
            chunk_metadata=chunk_metadata,
        )
    ]

    merged_relations: List[RelationRecord] = []
    for relation_list in chunk_results:
        if relation_list:
            merged_relations.extend(relation_list)

    if not merged_relations:
        return RelationsResponse(relations=[]), False

    deduplicated_relations: Dict[Tuple[str, str, str], RelationRecord] = {}
    for relation in merged_relations:
        dedup_key = (relation.subject, relation.subject_attribute or "", relation.object)
        if dedup_key not in deduplicated_relations:
            deduplicated_relations[dedup_key] = relation
            continue
        current_relation = deduplicated_relations[dedup_key]
        if (not (current_relation.display_name or "").strip()) and (relation.display_name or "").strip():
            deduplicated_relations[dedup_key] = relation
        elif len(relation.short_description or "") > len(current_relation.short_description or ""):
            deduplicated_relations[dedup_key] = relation

    semantic_relations = deduplicate_semantic_relations(list(deduplicated_relations.values()))
    final_relations = _sort_relations_by_iga_priority(
        semantic_relations,
        relevant_object_classes,
    )

    logger.info(
        "[Digester:Relations] Extraction process completed successfully with %d final relations", len(final_relations)
    )

    # update_job_progress(job_id, stage=JobStage.finished, message="Relation extraction complete")

    return RelationsResponse(relations=final_relations), bool(final_relations)
