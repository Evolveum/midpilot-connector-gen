# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Shared object-class confidence enrichment and final ordering."""

import json
import logging
from collections.abc import Mapping
from typing import Any, Dict, List, Optional, cast
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables.config import RunnableConfig

from src.core.llm import build_structured_chain, get_default_llm, make_basic_chain
from src.core.observability.langfuse import langfuse_handler
from src.documents.normalize import canonical_object_class_key
from src.jobs import append_job_error, update_job_progress
from src.modules.digester.aggregation.merges import merge_object_classes
from src.modules.digester.enums import ConfidenceLevel, RelevantLevel
from src.modules.digester.extraction.llm_execution import invoke_llm
from src.modules.digester.prompts.rest.object_class_relevancy_prompt import (
    get_object_classes_relevancy_system_prompt,
    get_object_classes_relevancy_user_prompt,
)
from src.modules.digester.prompts.rest.sorting_output_prompts import (
    sort_object_classes_system_prompt,
    sort_object_classes_user_prompt,
)
from src.modules.digester.prompts.sql.object_class_ranking_prompts import (
    sort_sql_object_classes_system_prompt,
    sort_sql_object_classes_user_prompt,
)
from src.modules.digester.schemas import (
    BaseObjectClass,
    ExtendedObjectClass,
    FinalObjectClass,
    ObjectClassConfidenceAssignmentsResponse,
    ObjectClassesConfidenceResponse,
    ObjectClassesRankedResponse,
    ObjectClassesResponse,
    ObjectClassNameOrderResponse,
    RankedObjectClass,
)
from src.shared.enums import GenerationIntent, JobStage

logger = logging.getLogger(__name__)

CONFIDENCE_ORDER: tuple[ConfidenceLevel, ...] = (
    ConfidenceLevel.HIGH,
    ConfidenceLevel.MEDIUM,
    ConfidenceLevel.LOW,
)
FALLBACK_CONFIDENCE: ConfidenceLevel = ConfidenceLevel.LOW


def _alpha_sort_key(obj_class: BaseObjectClass) -> str:
    return obj_class.name.strip().lower()


def _ranking_description(
    obj_class: ExtendedObjectClass,
    ranking_descriptions: Mapping[str, str] | None,
) -> str:
    if ranking_descriptions is None:
        return obj_class.description
    return ranking_descriptions.get(canonical_object_class_key(obj_class.name), obj_class.description)


def _to_confidence_payload(
    obj_class: ExtendedObjectClass,
    ranking_descriptions: Mapping[str, str] | None = None,
) -> Dict[str, Any]:
    return {
        "name": obj_class.name,
        "description": _ranking_description(obj_class, ranking_descriptions),
    }


def _to_ranked_object_class(
    extracted: ExtendedObjectClass,
    confidence: ConfidenceLevel,
) -> RankedObjectClass:
    return RankedObjectClass(
        name=extracted.name,
        description=extracted.description,
        superclass=extracted.superclass,
        abstract=extracted.abstract,
        embedded=extracted.embedded,
        relevant=RelevantLevel.TRUE,
        confidence=confidence,
    )


def _normalize_chunk_refs(chunks: Optional[List[Dict[str, Any]]]) -> List[Dict[str, str]]:
    unique_pairs: set[tuple[str, str]] = set()
    for chunk in chunks or []:
        doc_id = str(chunk.get("doc_id", "")).strip()
        chunk_id = str(chunk.get("chunk_id", "")).strip()
        if doc_id and chunk_id:
            unique_pairs.add((doc_id, chunk_id))

    return [{"doc_id": doc_id, "chunk_id": chunk_id} for doc_id, chunk_id in sorted(unique_pairs)]


def _to_final_object_class(
    ranked: RankedObjectClass,
    chunk_refs: List[Dict[str, str]],
) -> FinalObjectClass:
    return FinalObjectClass(
        name=ranked.name,
        description=ranked.description,
        superclass=ranked.superclass,
        abstract=ranked.abstract,
        embedded=ranked.embedded,
        relevant=ranked.relevant,
        confidence=ranked.confidence,
        relevant_documentations=chunk_refs,
    )


async def _sort_bucket_by_importance(
    object_classes: List[RankedObjectClass],
    confidence_level: ConfidenceLevel,
    *,
    sql_compact: bool = False,
    ranking_descriptions: Mapping[str, str] | None = None,
    intent: GenerationIntent = GenerationIntent.MANAGEMENT,
) -> List[RankedObjectClass]:
    if len(object_classes) <= 1:
        return list(object_classes)

    llm_sort = get_default_llm()
    if sql_compact:
        sort_chain = build_structured_chain(
            sort_sql_object_classes_system_prompt(intent),
            sort_sql_object_classes_user_prompt,
            ObjectClassNameOrderResponse,
            llm=llm_sort,
            user_role="human",
        )
    else:
        sort_chain = build_structured_chain(
            sort_object_classes_system_prompt(intent),
            sort_object_classes_user_prompt,
            ObjectClassesRankedResponse,
            llm=llm_sort,
            user_role="human",
        )

    original_map = {obj.name.strip().lower(): obj for obj in object_classes}
    alphabetical_bucket = sorted(object_classes, key=lambda item: item.name.strip().lower())
    if sql_compact:
        items_for_sorting = [
            {
                "name": item.name,
                "description": _ranking_description(item, ranking_descriptions),
            }
            for item in object_classes
        ]
        items_json = json.dumps(items_for_sorting, separators=(",", ":"))
    else:
        items_for_sorting = [
            item.model_dump(by_alias=True, exclude={"endpoints", "attributes"}) for item in object_classes
        ]
        items_json = json.dumps(items_for_sorting)

    try:
        logger.info(
            "[Digester:ObjectClasses] Sorting confidence bucket via LLM. Confidence: %s, items: %d",
            confidence_level,
            len(object_classes),
        )
        sort_result = await invoke_llm(
            sort_chain,
            {"items_json": items_json, "confidence_level": confidence_level},
            config=RunnableConfig(
                callbacks=[langfuse_handler],
                run_name="Digester:SortSqlObjectClasses" if sql_compact else "Digester:SortObjectClasses",
            ),
        )
        logger.debug("[Digester:ObjectClasses] Bucket sorting LLM raw (%s): %r", confidence_level, (sort_result or ""))

        if sort_result and sort_result.objectClasses:
            used: set[str] = set()
            sorted_bucket: List[RankedObjectClass] = []

            ordered_names = (
                sort_result.objectClasses
                if sql_compact
                else [ranked.name for ranked in cast(ObjectClassesRankedResponse, sort_result).objectClasses]
            )
            for ranked_name in ordered_names:
                key = ranked_name.strip().lower()
                if key in original_map and key not in used:
                    sorted_bucket.append(original_map[key])
                    used.add(key)

            for fallback in alphabetical_bucket:
                key = fallback.name.strip().lower()
                if key not in used:
                    sorted_bucket.append(fallback)

            return sorted_bucket

    except Exception as exc:
        logger.warning(
            "[Digester:ObjectClasses] Bucket sorting failed for confidence=%s, using alphabetical fallback: %s",
            confidence_level,
            exc,
        )

    return alphabetical_bucket


async def deduplicate_and_sort_object_classes(
    all_object_classes: List[ExtendedObjectClass],
    job_id: UUID,
    class_to_chunks: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    intent: GenerationIntent = GenerationIntent.MANAGEMENT,
) -> ObjectClassesResponse:
    """Apply the shared REST/SCIM confidence and ordering contract."""
    return await _deduplicate_and_sort_object_classes(
        all_object_classes,
        job_id,
        class_to_chunks,
        sql_compact=False,
        intent=intent,
    )


async def deduplicate_and_sort_sql_object_classes(
    all_object_classes: List[ExtendedObjectClass],
    job_id: UUID,
    class_to_chunks: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ranking_descriptions: Mapping[str, str] | None = None,
    intent: GenerationIntent = GenerationIntent.MANAGEMENT,
) -> ObjectClassesResponse:
    """Apply the same final ranking semantics with compact SQL-only LLM contracts."""
    return await _deduplicate_and_sort_object_classes(
        all_object_classes,
        job_id,
        class_to_chunks,
        sql_compact=True,
        ranking_descriptions=ranking_descriptions,
        intent=intent,
    )


async def _deduplicate_and_sort_object_classes(
    all_object_classes: List[ExtendedObjectClass],
    job_id: UUID,
    class_to_chunks: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    *,
    sql_compact: bool,
    ranking_descriptions: Mapping[str, str] | None = None,
    intent: GenerationIntent = GenerationIntent.MANAGEMENT,
) -> ObjectClassesResponse:
    """Deduplicate classes, assign LLM confidence, and apply final shared ordering.

    Final ordering:
    1. Confidence buckets: high -> medium -> low
    2. High-confidence classes: LLM ranking by importance for ``intent``
    3. Medium/low-confidence classes: alphabetical
    4. If LLM sorting fails: alphabetical fallback within the high bucket
    """
    logger.info("[Digester:ObjectClasses] Starting deduplication. Total count: %d", len(all_object_classes))
    dedup_list = cast(List[ExtendedObjectClass], merge_object_classes(all_object_classes))
    dedup_list.sort(key=_alpha_sort_key)
    logger.info("[Digester:ObjectClasses] Deduplication complete. Unique count: %d", len(dedup_list))

    if not dedup_list:
        await update_job_progress(job_id, stage=JobStage.sorting_finished, message="No object classes extracted")
        return ObjectClassesResponse(object_classes=[])

    confidence_map: Dict[str, ConfidenceLevel] = {}
    confidence_assignment_failed = False

    try:
        await update_job_progress(
            job_id,
            stage=JobStage.relevancy_filtering,
            message="Assigning confidence levels to object classes",
        )

        items_for_confidence = [
            _to_confidence_payload(oc, ranking_descriptions if sql_compact else None) for oc in dedup_list
        ]
        llm_filter = get_default_llm()
        confidence_model = ObjectClassConfidenceAssignmentsResponse if sql_compact else ObjectClassesConfidenceResponse
        confidence_parser: PydanticOutputParser[Any] = PydanticOutputParser(pydantic_object=confidence_model)

        developer_message = SystemMessage(
            content=get_object_classes_relevancy_system_prompt(compact_output=sql_compact, intent=intent)
            + "\n\n"
            + confidence_parser.get_format_instructions()
        )
        developer_message.additional_kwargs = {"__openai_role__": "developer"}

        confidence_json = (
            json.dumps(items_for_confidence, separators=(",", ":")) if sql_compact else json.dumps(items_for_confidence)
        )
        user_message = HumanMessage(
            content=get_object_classes_relevancy_user_prompt(
                confidence_json,
                source_description="a database schema" if sql_compact else "API documentation",
                intent=intent,
            )
        )
        user_message.additional_kwargs = {"__openai_role__": "user"}

        chat_prompts = ChatPromptTemplate.from_messages([developer_message, user_message])
        confidence_chain = make_basic_chain(prompt=chat_prompts, llm=llm_filter, parser=confidence_parser)

        confidence_result = await invoke_llm(
            confidence_chain,
            {},
            config=RunnableConfig(
                callbacks=[langfuse_handler],
                run_name=("Digester:SqlObjectClassConfidence" if sql_compact else "Digester:ObjectClassConfidence"),
            ),
        )
        logger.info("[Digester:ObjectClasses] Confidence LLM raw: %r", (confidence_result or ""))

        if confidence_result and confidence_result.objectClasses:
            for confidence_info in confidence_result.objectClasses:
                key = confidence_info.name.strip().lower()
                if key:
                    confidence_map[key] = confidence_info.confidence

        await update_job_progress(
            job_id,
            stage=JobStage.relevancy_filtering_finished,
            message="Confidence assignment finished",
        )

    except Exception as exc:
        confidence_assignment_failed = True
        error_message = f"[Digester:ObjectClasses] Confidence assignment failed: {exc}"
        logger.exception(error_message)
        await append_job_error(job_id, error_message)
        await update_job_progress(
            job_id,
            stage=JobStage.relevancy_filtering_finished,
            message="Confidence assignment failed; using fallback confidence",
        )

    ranked_list: List[RankedObjectClass] = []
    for extracted in dedup_list:
        normalized_name = extracted.name.strip().lower()
        confidence = confidence_map.get(normalized_name, FALLBACK_CONFIDENCE)
        if normalized_name not in confidence_map:
            logger.debug(
                "[Digester:ObjectClasses] Missing confidence for class '%s'; using fallback '%s'",
                extracted.name,
                FALLBACK_CONFIDENCE,
            )
        ranked_list.append(_to_ranked_object_class(extracted, confidence))

    try:
        await update_job_progress(
            job_id,
            stage=JobStage.sorting,
            message="Sorting object classes by confidence and importance",
        )

        sorted_ranked: List[RankedObjectClass] = []
        for level in CONFIDENCE_ORDER:
            bucket = [obj for obj in ranked_list if obj.confidence == level]
            if not bucket:
                continue
            if level == ConfidenceLevel.HIGH:
                sorted_bucket = await _sort_bucket_by_importance(
                    bucket,
                    level,
                    sql_compact=sql_compact,
                    ranking_descriptions=ranking_descriptions,
                    intent=intent,
                )
            else:
                sorted_bucket = sorted(bucket, key=lambda item: item.name.strip().lower())
            sorted_ranked.extend(sorted_bucket)

        final_sorted = [
            _to_final_object_class(
                ranked=item,
                chunk_refs=_normalize_chunk_refs(
                    (class_to_chunks or {}).get(canonical_object_class_key(item.name), [])
                ),
            )
            for item in sorted_ranked
        ]

        await update_job_progress(
            job_id,
            stage=JobStage.sorting_finished,
            message="Sorting finished; finalizing",
        )
        logger.info(
            "[Digester:ObjectClasses] Final ordering complete. Count: %d (confidence fallback used: %s)",
            len(final_sorted),
            confidence_assignment_failed,
        )
        return ObjectClassesResponse(object_classes=final_sorted)

    except Exception as exc:
        error_message = f"[Digester:ObjectClasses] Sorting failed, using deterministic fallback: {exc}"
        logger.exception(error_message)
        await append_job_error(job_id, error_message)

    fallback_ranked = sorted(
        ranked_list,
        key=lambda item: (CONFIDENCE_ORDER.index(item.confidence), item.name.strip().lower()),
    )
    fallback_final = [
        _to_final_object_class(
            ranked=item,
            chunk_refs=_normalize_chunk_refs((class_to_chunks or {}).get(canonical_object_class_key(item.name), [])),
        )
        for item in fallback_ranked
    ]
    await update_job_progress(
        job_id,
        stage=JobStage.sorting_finished,
        message="Using fallback ordering by confidence and name",
    )
    return ObjectClassesResponse(object_classes=fallback_final)
