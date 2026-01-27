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

from ....common.enums import JobStage
from ....common.jobs import append_job_error, update_job_progress
from ....common.langfuse import langfuse_handler
from ....common.llm import get_default_llm, make_basic_chain
from ..prompts.object_class_prompts import (
    get_object_class_system_prompt,
    get_object_class_user_prompt,
)
from ..prompts.object_class_relevancy_prompt import (
    get_object_classes_relevancy_system_prompt,
    get_object_classes_relevancy_user_prompt,
)
from ..prompts.sorting_output_prompts import (
    sort_object_classes_system_prompt,
    sort_object_classes_user_prompt,
)
from ..schema import ObjectClass, ObjectClassesRelevancyResponse, ObjectClassesResponse
from ..utils.merges import merge_object_classes
from ..utils.parallel import run_extraction_parallel

logger = logging.getLogger(__name__)


async def extract_object_classes_raw(
    schema: str, job_id: UUID, doc_id: Optional[UUID] = None, doc_metadata: Optional[Dict[str, Any]] = None
) -> Tuple[List[ObjectClass], bool]:
    """
    Extract raw object classes from a single document with per-chunk parallel LLM calls.
    Does NOT deduplicate or sort - that's done later across all documents.

    Args:
        schema: The document content to extract from
        job_id: Job ID for progress tracking
        doc_id: Optional document UUID
        doc_metadata: Optional metadata dict containing summary and @metadata with llm_tags

    Returns:
        - List of raw ObjectClass instances (with relevant_chunks populated)
        - Boolean indicating if relevant data was found
    """

    def parse_fn(result: ObjectClassesResponse) -> List[ObjectClass]:
        return result.objectClasses or []

    extracted, has_relevant_data = await run_extraction_parallel(
        schema=schema,
        pydantic_model=ObjectClassesResponse,
        system_prompt=get_object_class_system_prompt,
        user_prompt=get_object_class_user_prompt,
        parse_fn=parse_fn,
        logger_prefix="[Digester:ObjectClasses] ",
        job_id=job_id,
        doc_id=doc_id,
        track_chunk_per_item=True,
        chunk_metadata=doc_metadata,
    )

    extracted_valid: List[ObjectClass] = []

    # Validate extracted object classes by checking if names exist in the schema
    for obj_class in extracted:
        if obj_class.name and obj_class.name.strip():
            if re.search(re.escape(obj_class.name.strip()) + r"(\s|\/|\'|s\s|s\'|s\/)", schema, re.IGNORECASE):
                extracted_valid.append(obj_class)
            else:
                logger.info(
                    "[Digester:ObjectClasses] Extracted object class name '%s' not found in document, deleting object class",
                    obj_class.name,
                )

    logger.info("[Digester:ObjectClasses] Raw extraction complete from document. Count: %d", len(extracted_valid))
    return extracted_valid, bool(extracted_valid)


async def deduplicate_and_sort_object_classes(
    all_object_classes: List[ObjectClass],
    job_id: UUID,
    filter_relevancy: bool,
    min_relevancy_level: str,
    class_to_chunks: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> ObjectClassesResponse:
    """
    Deduplicate and sort object classes from all documents.
    If chosen, use LLM relevancy filtering.
    For now we only filter based on relevancy levels, but in the future this will be added as metadata to each class.

    Args:
        all_object_classes: List of ObjectClass instances from all documents
        job_id: Optional job ID for progress tracking
        class_to_chunks: Optional mapping of class names to their relevant chunks
        filter_relevancy: Whether to use LLM to filter based on generated relevancy levels
        min_relevancy_level: Minimum relevancy level to include an object class, options are:
            "low", "medium", "high", this is only used if filter_relevancy is True
            default is "medium"

    Returns:
        ObjectClassesResponse with deduplicated and sorted classes
    """
    logger.info("[Digester:ObjectClasses] Starting deduplication and sorting. Total count: %d", len(all_object_classes))
    dedup_list: List[ObjectClass] = merge_object_classes(all_object_classes, class_to_chunks)
    logger.info("[Digester:ObjectClasses] Deduplication complete. Unique count: %d", len(dedup_list))

    dedup_list.sort(key=lambda x: x.name.lower())

    if filter_relevancy:
        # Filters object classes by relevancy using LLM
        try:
            update_job_progress(
                job_id,
                stage=JobStage.relevancy_filtering,
                message="Filtering object classes by relevancy via LLM",
            )
            logger.info(
                "[Digester:ObjectClasses] Filtering by relevancy via LLM. Items count: %d, minimum relevancy: %s",
                len(dedup_list),
                min_relevancy_level,
            )

            items_for_filtering = [
                {
                    "name": oc.name,
                    "description": oc.description,
                    "superclass": oc.superclass,
                    "abstract": oc.abstract,
                    "embedded": oc.embedded,
                    "relevant_chunks": [{"docUuid": str(chunk["docUuid"])} for chunk in (oc.relevant_chunks or [])],
                }
                for oc in dedup_list
            ]

            llm_filter = get_default_llm()
            relevancy_parser: PydanticOutputParser[ObjectClassesRelevancyResponse] = PydanticOutputParser(
                pydantic_object=ObjectClassesRelevancyResponse
            )

            developer_message = SystemMessage(
                content=get_object_classes_relevancy_system_prompt()
                + "\n\n"
                + relevancy_parser.get_format_instructions()
            )
            developer_message.additional_kwargs = {"__openai_role__": "developer"}

            user_message = HumanMessage(
                content=get_object_classes_relevancy_user_prompt(json.dumps(items_for_filtering))
            )
            user_message.additional_kwargs = {"__openai_role__": "user"}

            chat_prompts = ChatPromptTemplate.from_messages(
                [
                    developer_message,
                    user_message,
                ]
            )

            relevancy_chain = make_basic_chain(prompt=chat_prompts, llm=llm_filter, parser=relevancy_parser)

            relevancy_result: ObjectClassesRelevancyResponse = cast(
                ObjectClassesRelevancyResponse,
                await relevancy_chain.ainvoke({}, config=RunnableConfig(callbacks=[langfuse_handler])),
            )

            logger.info("[Digester:ObjectClasses] Relevancy LLM raw: %r", (relevancy_result or ""))

            curr_object_classes_map: dict[str, ObjectClass] = {
                obj_class.name.strip().lower(): obj_class for obj_class in dedup_list
            }
            filtered_object_classes: list[ObjectClass] = []

            if relevancy_result and relevancy_result.objectClasses:
                for relevancy_info in relevancy_result.objectClasses:
                    key = relevancy_info.name.strip().lower()

                    if key in curr_object_classes_map.keys():
                        # TODO: Optimilize, ugly code
                        if min_relevancy_level == "low":
                            filtered_object_classes.append(curr_object_classes_map[key])
                        elif min_relevancy_level == "medium" and relevancy_info.relevant in ["medium", "high"]:
                            filtered_object_classes.append(curr_object_classes_map[key])
                        elif min_relevancy_level == "high" and relevancy_info.relevant == "high":
                            filtered_object_classes.append(curr_object_classes_map[key])

                dedup_list = filtered_object_classes
                logger.info("[Digester:ObjectClasses] Relevancy filtering complete. Final count: %d", len(dedup_list))
                update_job_progress(
                    job_id, stage=JobStage.relevancy_filtering_finished, message="Relevancy filtering finished"
                )

        except Exception as e:
            logger.exception("[Digester:ObjectClasses] Relevancy filtering failed. Error: %s", e)
            update_job_progress(
                job_id, stage=JobStage.relevancy_filtering_finished, message="Relevancy filtering failed"
            )
            append_job_error(job_id, f"[Digester:ObjectClasses] Relevancy filtering failed: {e}")

    # Try to sort by importance using LLM, but fall back to alphabetical if it fails
    try:
        update_job_progress(
            job_id,
            stage=JobStage.sorting,
            message="Processing chunks finished; now sorting by importance",
        )

        if not dedup_list:
            return ObjectClassesResponse(object_classes=[])

        parser: PydanticOutputParser[ObjectClassesResponse] = PydanticOutputParser(
            pydantic_object=ObjectClassesResponse
        )
        llm_sort = get_default_llm()
        sort_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", sort_object_classes_system_prompt + "\n\n{format_instructions}"),
                ("human", sort_object_classes_user_prompt),
            ]
        ).partial(format_instructions=parser.get_format_instructions())
        sort_chain = make_basic_chain(sort_prompt, llm_sort, parser)

        # Send only name and description to LLM to save tokens
        items_for_sorting = [{"name": oc.name, "description": oc.description} for oc in dedup_list]
        items_json = json.dumps(items_for_sorting)
        logger.info("[Digester:ObjectClasses] Sorting via LLM. Items count: %d", len(dedup_list))

        try:
            sort_result = cast(
                ObjectClassesResponse,
                await sort_chain.ainvoke(
                    {"items_json": items_json}, config=RunnableConfig(callbacks=[langfuse_handler])
                ),
            )
            logger.debug("[Digester:ObjectClasses] Sorting LLM raw: %r", (sort_result or ""))

            if sort_result and sort_result.objectClasses:
                # Map original objects by normalized name to restore full data after sorting
                original_map = {oc.name.strip().lower(): oc for oc in dedup_list}
                used: set[str] = set()
                sorted_filtered: List[ObjectClass] = []
                for oc in sort_result.objectClasses:
                    k = oc.name.strip().lower()
                    if k in original_map and k not in used:
                        # Use the original object with all fields intact
                        sorted_filtered.append(original_map[k])
                        used.add(k)
                # Append any originals not referenced by the sorter (preserve base order)
                for oc in dedup_list:
                    k = oc.name.strip().lower()
                    if k not in used:
                        sorted_filtered.append(oc)

                logger.info("[Digester:ObjectClasses] Sorting complete. Final count: %d", len(sorted_filtered))
                update_job_progress(job_id, stage=JobStage.sorting_finished, message="Sorting finished; finalizing")
                return ObjectClassesResponse(object_classes=sorted_filtered)

            logger.warning("[Digester:ObjectClasses] Sorting LLM returned empty; using alphabetical order.")
        except Exception as e:
            logger.warning(
                "[Digester:ObjectClasses] LLM sorting failed, falling back to alphabetical order: %s", str(e)
            )

    except Exception as exc:
        logger.exception("[Digester:ObjectClasses] Sorting failed, using alphabetical order. Error: %s", exc)
        append_job_error(job_id, f"[Digester:ObjectClasses] Sorting failed, using alphabetical order: {exc}")

    # Fallback to alphabetical order
    update_job_progress(job_id, stage=JobStage.sorting_finished, message="Using alphabetical order")
    return ObjectClassesResponse(object_classes=dedup_list)
