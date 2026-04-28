# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple, cast
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables.config import RunnableConfig

from src.common.enums import JobStage
from src.common.jobs import append_job_error, update_job_progress
from src.common.langfuse import langfuse_handler
from src.common.llm import get_default_llm, make_basic_chain
from src.modules.digester.enums import ConfidenceLevel, RelevantLevel
from src.modules.digester.prompts.rest.object_class_prompts import (
    get_object_class_system_prompt,
    get_object_class_user_prompt,
)
from src.modules.digester.prompts.rest.object_class_relevancy_prompt import (
    get_object_classes_relevancy_system_prompt,
    get_object_classes_relevancy_user_prompt,
)
from src.modules.digester.prompts.rest.sorting_output_prompts import (
    sort_object_classes_system_prompt,
    sort_object_classes_user_prompt,
)
from src.modules.digester.schema import (
    BaseObjectClass,
    ExtendedObjectClass,
    FinalObjectClass,
    ObjectClassesConfidenceResponse,
    ObjectClassesExtendedResponse,
    ObjectClassesRankedResponse,
    ObjectClassesResponse,
    RankedObjectClass,
)
from src.modules.digester.utils.chunk_extraction import build_chunk_extraction_chain, extract_single_chunk
from src.modules.digester.utils.merges import merge_object_classes

logger = logging.getLogger(__name__)

CONFIDENCE_ORDER: tuple[ConfidenceLevel, ...] = (
    ConfidenceLevel.HIGH,
    ConfidenceLevel.MEDIUM,
    ConfidenceLevel.LOW,
)
FALLBACK_CONFIDENCE: ConfidenceLevel = ConfidenceLevel.LOW


def build_object_class_extraction_chain() -> Any:
    """Build the reusable chain for REST object-class extraction across chunks."""
    return build_chunk_extraction_chain(
        pydantic_model=ObjectClassesExtendedResponse,
        system_prompt=get_object_class_system_prompt,
        user_prompt=get_object_class_user_prompt,
    )


def _alpha_sort_key(obj_class: BaseObjectClass) -> str:
    return obj_class.name.strip().lower()


def _to_confidence_payload(obj_class: ExtendedObjectClass) -> Dict[str, Any]:
    return {
        "name": obj_class.name,
        "description": obj_class.description,
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
) -> List[RankedObjectClass]:
    if len(object_classes) <= 1:
        return list(object_classes)

    parser: PydanticOutputParser[ObjectClassesRankedResponse] = PydanticOutputParser(
        pydantic_object=ObjectClassesRankedResponse
    )
    llm_sort = get_default_llm()
    sort_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", sort_object_classes_system_prompt + "\n\n{format_instructions}"),
            ("human", sort_object_classes_user_prompt),
        ]
    ).partial(format_instructions=parser.get_format_instructions())
    sort_chain = make_basic_chain(sort_prompt, llm_sort, parser)

    original_map = {obj.name.strip().lower(): obj for obj in object_classes}
    alphabetical_bucket = sorted(object_classes, key=lambda item: item.name.strip().lower())
    items_for_sorting = [item.model_dump(by_alias=True, exclude={"endpoints", "attributes"}) for item in object_classes]
    items_json = json.dumps(items_for_sorting)

    try:
        logger.info(
            "[Digester:ObjectClasses] Sorting confidence bucket via LLM. Confidence: %s, items: %d",
            confidence_level,
            len(object_classes),
        )
        sort_result = cast(
            ObjectClassesRankedResponse,
            await sort_chain.ainvoke(
                {"items_json": items_json, "confidence_level": confidence_level},
                config=RunnableConfig(callbacks=[langfuse_handler]),
            ),
        )
        logger.debug("[Digester:ObjectClasses] Bucket sorting LLM raw (%s): %r", confidence_level, (sort_result or ""))

        if sort_result and sort_result.objectClasses:
            used: set[str] = set()
            sorted_bucket: List[RankedObjectClass] = []

            for ranked in sort_result.objectClasses:
                key = ranked.name.strip().lower()
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


async def extract_object_classes_raw(
    schema: str,
    job_id: UUID,
    chunk_id: Optional[UUID] = None,
    chunk_metadata: Optional[Dict[str, Any]] = None,
    extraction_chain: Any | None = None,
) -> Tuple[List[ExtendedObjectClass], bool]:
    """
    Extract raw object classes from a single chunk with one LLM call.
    Does NOT deduplicate or sort - that's done later across all chunks.

    Args:
        schema: Chunk content to extract from.
        job_id: Job ID for progress tracking.
        chunk_id: Optional chunk UUID.
        chunk_metadata: Optional metadata for summary/tag prompt context.
        extraction_chain: Optional pre-built reusable extraction chain.
    """

    def parse_fn(result: ObjectClassesExtendedResponse) -> List[ExtendedObjectClass]:
        return result.objectClasses or []

    extracted, has_relevant_data = await extract_single_chunk(
        schema=schema,
        pydantic_model=ObjectClassesExtendedResponse,
        system_prompt=get_object_class_system_prompt,
        user_prompt=get_object_class_user_prompt,
        parse_fn=parse_fn,
        logger_prefix="[Digester:REST:ObjectClasses] ",
        job_id=job_id,
        chunk_id=chunk_id,
        track_chunk_per_item=True,
        chunk_metadata=chunk_metadata,
        extraction_chain=extraction_chain,
    )

    extracted_valid: List[ExtendedObjectClass] = []

    # Validate extracted object classes by checking if names exist in the schema
    for obj_class in extracted:
        if obj_class.name and obj_class.name.strip():
            if re.search(re.escape(obj_class.name.strip()) + r'[\s\n\t.,;:!?\-\)\]\}"\']', schema, re.IGNORECASE):
                extracted_valid.append(obj_class)
            else:
                logger.info(
                    "[Digester:ObjectClasses] Extracted object class name '%s' not found in chunk, deleting object class",
                    obj_class.name,
                )

    logger.info("[Digester:ObjectClasses] Raw extraction complete from chunk. Count: %d", len(extracted_valid))
    return extracted_valid, bool(extracted_valid)


async def deduplicate_and_sort_object_classes(
    all_object_classes: List[ExtendedObjectClass],
    job_id: UUID,
    class_to_chunks: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> ObjectClassesResponse:
    """
    Deduplicate object classes, assign confidence, and sort final output.

    Final ordering:
    1. Confidence buckets: high -> medium -> low
    2. Inside each bucket: LLM ranking by IGA/IDM importance
    3. If sorting fails: alphabetical fallback within that bucket
    """
    logger.info("[Digester:ObjectClasses] Starting deduplication. Total count: %d", len(all_object_classes))
    dedup_list = cast(List[ExtendedObjectClass], merge_object_classes(all_object_classes, class_to_chunks))
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

        items_for_confidence = [_to_confidence_payload(oc) for oc in dedup_list]
        llm_filter = get_default_llm()
        confidence_parser: PydanticOutputParser[ObjectClassesConfidenceResponse] = PydanticOutputParser(
            pydantic_object=ObjectClassesConfidenceResponse
        )

        developer_message = SystemMessage(
            content=get_object_classes_relevancy_system_prompt() + "\n\n" + confidence_parser.get_format_instructions()
        )
        developer_message.additional_kwargs = {"__openai_role__": "developer"}

        user_message = HumanMessage(content=get_object_classes_relevancy_user_prompt(json.dumps(items_for_confidence)))
        user_message.additional_kwargs = {"__openai_role__": "user"}

        chat_prompts = ChatPromptTemplate.from_messages([developer_message, user_message])
        confidence_chain = make_basic_chain(prompt=chat_prompts, llm=llm_filter, parser=confidence_parser)

        confidence_result = cast(
            ObjectClassesConfidenceResponse,
            await confidence_chain.ainvoke({}, config=RunnableConfig(callbacks=[langfuse_handler])),
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
        append_job_error(job_id, error_message)
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
            message="Sorting object classes by confidence and IGA/IDM importance",
        )

        sorted_ranked: List[RankedObjectClass] = []
        for level in CONFIDENCE_ORDER:
            bucket = [obj for obj in ranked_list if obj.confidence == level]
            if not bucket:
                continue
            if level == ConfidenceLevel.HIGH:
                sorted_bucket = await _sort_bucket_by_importance(bucket, level)
            else:
                sorted_bucket = sorted(bucket, key=lambda item: item.name.strip().lower())
            sorted_ranked.extend(sorted_bucket)

        final_sorted = [
            _to_final_object_class(
                ranked=item,
                chunk_refs=_normalize_chunk_refs((class_to_chunks or {}).get(item.name.strip().lower(), [])),
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
        append_job_error(job_id, error_message)

    fallback_ranked = sorted(
        ranked_list,
        key=lambda item: (CONFIDENCE_ORDER.index(item.confidence), item.name.strip().lower()),
    )
    fallback_final = [
        _to_final_object_class(
            ranked=item,
            chunk_refs=_normalize_chunk_refs((class_to_chunks or {}).get(item.name.strip().lower(), [])),
        )
        for item in fallback_ranked
    ]
    await update_job_progress(
        job_id,
        stage=JobStage.sorting_finished,
        message="Using fallback ordering by confidence and name",
    )
    return ObjectClassesResponse(object_classes=fallback_final)
