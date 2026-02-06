# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

from ....common.chunks import normalize_to_text
from ....common.jobs import append_job_error, update_job_progress
from ....common.langfuse import langfuse_handler
from ....common.llm import get_default_llm, make_basic_chain
from ..prompts.relations_prompts import (
    get_relations_system_prompt,
    get_relations_user_prompt,
)
from ..schema import RelationRecord, RelationsResponse
from ..utils.metadata_helper import extract_summary_and_tags

logger = logging.getLogger(__name__)


def _extract_relevant_names(relevant_object_classes: Any) -> List[Tuple[str, str]]:
    """
    Extract relevant class names and descriptions from the object classes payload where relevant == 'true'.
    Returns a list of tuples (name, description).
    Handles various input formats: JSON string, dict, list, or ObjectClassesResponse.
    """
    try:
        if hasattr(relevant_object_classes, "objectClasses"):
            return [
                (obj.name, obj.description or "")
                for obj in relevant_object_classes.object_classes
                if obj.relevant == "true"
            ]

        # Handle JSON string
        if isinstance(relevant_object_classes, str):
            logger.debug("[Digester:Relations] Parsing JSON string (relevant object classes)")
            parsed_data = json.loads(relevant_object_classes)
        else:
            parsed_data = relevant_object_classes

        # Handle dict with objectClasses or object_classes key
        if isinstance(parsed_data, dict):
            object_classes = parsed_data.get("objectClasses") or parsed_data.get("objectClasses", [])
            logger.debug(
                "[Digester:Relations] Found %d object classes in dict relevant_object_classes", len(object_classes)
            )
        elif isinstance(parsed_data, list):
            object_classes = parsed_data
            logger.debug(
                "[Digester:Relations] Processing list relevant_object_classes with %d items", len(object_classes)
            )
        else:
            logger.warning(
                "[Digester:Relations] Unsupported relevant_object_classes format: %s", type(parsed_data).__name__
            )
            return []

        # Extract names and descriptions where relevant == "true"
        relevant_items = []
        for obj_class in object_classes:
            if isinstance(obj_class, dict):
                if obj_class.get("relevant") == "true":
                    class_name = obj_class.get("name")
                    class_desc = obj_class.get("description", "") or ""
                    if class_name:
                        relevant_items.append((class_name, class_desc))
            elif hasattr(obj_class, "relevant") and hasattr(obj_class, "name"):
                if obj_class.relevant == "true":
                    class_desc = getattr(obj_class, "description", "") or ""
                    relevant_items.append((obj_class.name, class_desc))

        logger.debug("[Digester:Relations] Successfully extracted %d relevant items", len(relevant_items))
        return relevant_items

    except Exception as e:
        logger.error("[Digester:Relations] Failed to extract relevant names: %s", e)
        return []


def _parse_relations_result(
    result: Any,
    job_id: UUID,
    idx: Optional[int] = None,
    total_chunks: Optional[int] = None,
    doc_id: Optional[UUID] = None,
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

    except Exception as e:
        logger.error("[Digester:Relations] Failed to parse relations result: %s", e)
        if job_id is not None and idx is not None:
            try:
                total = total_chunks or 0
                prefix = "[Digester:Relations] "
                error_msg = f"{prefix}Parse failed for chunk {idx + 1}/{total if total else '?'}: {e}"
                if doc_id:
                    error_msg = f"{error_msg} (Doc: {doc_id})"
                append_job_error(job_id, error_msg)
            except Exception:
                pass
        return []


async def _extract_from_chunk(
    chain,
    idx: int,
    chunk: str,
    job_id: UUID,
    total_chunks: Optional[int] = None,
    doc_id: Optional[UUID] = None,
    doc_metadata: Optional[Dict[str, Any]] = None,
) -> List[RelationRecord]:
    try:
        logger.info("[Digester:Relations] LLM call for chunk %s", idx + 1)

        # Extract summary and tags from doc metadata
        summary, tags = extract_summary_and_tags(doc_metadata)

        result = await chain.ainvoke(
            {"chunk": chunk, "summary": summary, "tags": tags}, config={"callbacks": [langfuse_handler]}
        )
        return _parse_relations_result(result, job_id=job_id, idx=idx, total_chunks=total_chunks, doc_id=doc_id)
    except Exception as e:
        logger.error("[Digester:Relations] Chunk %d failed: %s", idx + 1, e)
        total = total_chunks or 0
        error_msg = f"[Digester:Relations] Chunk {idx + 1}/{total if total else '?'} call failed: {e}"
        if doc_id:
            error_msg = f"{error_msg} (Doc: {doc_id})"
        append_job_error(job_id, error_msg)
        return []


async def extract_relations(
    schema: str,
    relevant_object_classes: Any,
    job_id: UUID,
    doc_id: Optional[UUID] = None,
    doc_metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[RelationsResponse, bool]:
    """
    Extract relationships between object classes from an OpenAPI/Swagger specification.

    This function analyzes the provided OpenAPI spec to identify relationships where one object class
    contains properties that reference another object class (foreign keys, IDs, $refs, etc.).

    Args:
        schema: OpenAPI/Swagger specification as string (YAML or JSON format).
        relevant_object_classes: Output from /getObjectClass endpoint. Can be JSON string, dict, list,
                              or ObjectClassesResponse. Only items with relevant == "true" are processed.

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
    logger.info("[Digester:Relations] Starting relation extraction process")
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

    # Normalize text (document is already pre-chunked in DB)
    text = normalize_to_text(schema)

    if not text or not text.strip():
        logger.warning("[Digester:Relations] Empty schema provided; returning empty.")
        return RelationsResponse(relations=[]), False

    # Progress: start processing
    await update_job_progress(
        job_id,
        stage="processing_chunks",
        message="Processing document and extracting relations",
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

    logger.info("[Digester:Relations] Processing document for relation extraction")
    # Process the single pre-chunked document (no need for asyncio.gather with just one item)
    chunk_results = [
        await _extract_from_chunk(chain, 0, text, job_id, total_chunks=1, doc_id=doc_id, doc_metadata=doc_metadata)
    ]
    logger.info("[Digester:Relations] Extraction completed")

    # update_job_progress(
    #     job_id,
    #     stage="merging",
    #     message="Merging and deduplicating relations",
    # )

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
        if (not (current_relation.name or "").strip()) and (relation.name or "").strip():
            deduplicated_relations[dedup_key] = relation
        elif len(relation.short_description or "") > len(current_relation.short_description or ""):
            deduplicated_relations[dedup_key] = relation

    final_relations = list(deduplicated_relations.values())
    final_relations.sort(key=lambda x: (x.subject, x.subject_attribute or "", x.object))

    logger.info(
        "[Digester:Relations] Extraction process completed successfully with %d final relations", len(final_relations)
    )

    # update_job_progress(job_id, stage=JobStage.finished, message="Relation extraction complete")

    return RelationsResponse(relations=final_relations), bool(final_relations)
