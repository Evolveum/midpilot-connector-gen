# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import json
import logging
import math
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar, cast
from uuid import UUID

from fuzzysearch import find_near_matches  # type: ignore
from langchain_core.output_parsers import BaseOutputParser, PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables.config import RunnableConfig
from pydantic import BaseModel, ValidationError

from ....common.chunks import normalize_to_text
from ....common.enums import JobStage
from ....common.jobs import append_job_error, update_job_progress
from ....common.langfuse import langfuse_handler
from ....common.llm import get_default_llm, make_basic_chain
from ....config import config
from .metadata_helper import extract_summary_and_tags

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


def _calculate_fuzzy_error_budget(marker: str, fuzzy_marker_error_ratio: float) -> int:
    stripped_marker = marker.strip()
    if not stripped_marker:
        return 0
    return max(1, math.floor(len(stripped_marker) * fuzzy_marker_error_ratio))


def _find_best_fuzzy_literal(
    text: str, marker: str, start_pos: int = 0, fuzzy_marker_error_ratio: float = 0.05
) -> Optional[Any]:
    if not marker.strip() or start_pos >= len(text):
        return None

    max_errors = _calculate_fuzzy_error_budget(marker, fuzzy_marker_error_ratio)
    matches = [m for m in find_near_matches(marker, text, max_l_dist=max_errors) if m.start >= start_pos]
    if not matches:
        return None

    return min(matches, key=lambda match: (match.dist, match.start))


def _find_closest_best_fuzzy_literal(
    text: str, marker: str, start_pos: int, fuzzy_marker_error_ratio: float = 0.05, max_length: int = 10000
) -> Optional[Any]:
    if not marker.strip() or start_pos >= len(text):
        return None

    max_errors = _calculate_fuzzy_error_budget(marker, fuzzy_marker_error_ratio)
    matches = [
        m
        for m in find_near_matches(marker, text, max_l_dist=max_errors)
        if m.start >= start_pos and m.end - start_pos <= max_length
    ]
    if not matches:
        return None

    # Selection rule:
    # 1) lowest Levenshtein distance
    # 2) if fuzziness ties, choose the closest one after start_pos
    return min(
        matches,
        key=lambda match: (
            match.dist,
            max(0, match.start - start_pos),
        ),
    )


def _result_snippet(result: Any, limit: int = 2000) -> str:
    """Best-effort stringify of a pydantic model or arbitrary object for logging/errors."""
    try:
        if hasattr(result, "model_dump_json"):
            raw = result.model_dump_json()  # pydantic v2
        elif hasattr(result, "model_dump"):
            raw = json.dumps(result.model_dump(by_alias=True, mode="json"))
        else:
            raw = getattr(result, "content", None) or repr(result)
    except Exception:
        raw = repr(result)
    raw = raw if isinstance(raw, str) else str(raw)
    return raw if len(raw) <= limit else raw[:limit] + "...(truncated)"


async def run_extraction_parallel(
    schema: str,
    pydantic_model: type[T],
    system_prompt: str,
    user_prompt: str,
    parse_fn: Callable[[T], List[Any]],
    job_id: UUID,
    logger_prefix: str = "",
    doc_id: Optional[UUID] = None,
    track_chunk_per_item: bool = False,
    chunk_metadata: Optional[Dict[str, Any]] = None,
    enabled_sequence_checking: bool = False,
    fuzzy_marker_error_ratio: Optional[float] = None,
    sequence_max_length: Optional[int] = None,
) -> Tuple[List[Any], bool]:
    """
    Run LLM extraction on a pre-chunked document.

    Since documents are already pre-chunked in the DB (max 20000 tokens),
    no further splitting is needed - we process the document as-is.

    Args:
        schema: The pre-chunked document text to be processed
        pydantic_model: The Pydantic model to validate the extracted data against
        system_prompt: The system prompt to use for the LLM
        user_prompt: The user prompt template to use for the LLM
        parse_fn: Function to parse the Pydantic model into a list of items
        job_id: ID of the job for progress tracking
        logger_prefix: Optional prefix for log messages
        doc_id: Optional document ID for tracking
        track_chunk_per_item: Deprecated (kept for backward compatibility, always sets index to 0)
        chunk_metadata: Optional metadata about the document (summary, tags, etc.)
        enabled_sequence_checking: Whether to enable checking if the start and end of the sequence are valid (only once present in a chunk)
    Returns:
        - Flat list of extracted items
        - Boolean indicating if any relevant data was found
    """
    # Normalize text (document is already a single pre-chunked unit)
    text = normalize_to_text(schema)
    digester_config = config.digester
    fuzzy_marker_error_ratio = (
        fuzzy_marker_error_ratio if fuzzy_marker_error_ratio is not None else digester_config.fuzzy_marker_error_ratio
    )
    sequence_max_length = (
        sequence_max_length if sequence_max_length is not None else digester_config.sequence_max_length
    )

    # Progress: start processing
    await update_job_progress(
        job_id,
        stage=JobStage.processing_chunks,
        message="Processing document and extracting relevant information",
    )

    # Build LLM chain
    parser: BaseOutputParser = PydanticOutputParser(pydantic_object=pydantic_model)
    llm = get_default_llm()
    prompt = ChatPromptTemplate.from_messages(
        [("system", system_prompt + "\n\n{format_instructions}"), ("human", user_prompt)]
    ).partial(format_instructions=parser.get_format_instructions())
    chain = make_basic_chain(prompt, llm, parser)

    # Process the document (already pre-chunked)
    try:
        logger.info("%sCalling LLM for document extraction.", logger_prefix)

        # Extract summary and tags from document metadata
        summary, tags = extract_summary_and_tags(chunk_metadata)

        result = cast(
            T,
            await chain.ainvoke(
                {"chunk": text, "summary": summary, "tags": tags},
                config=RunnableConfig(callbacks=[langfuse_handler]),
            ),
        )
        logger.debug("%sLLM result: %r", logger_prefix, (result or ""))

        if not result:
            logger.warning("%sEmpty LLM response.", logger_prefix)
            error_msg = f"{logger_prefix}Empty LLM response."
            if doc_id:
                error_msg = f"{error_msg} (Doc: {doc_id})"
            append_job_error(job_id, error_msg)
            return [], False

        # Parse structured output
        try:
            items = parse_fn(result)
        except (ValidationError, ValueError, json.JSONDecodeError) as e:
            logger.info("%sJSON parse failed. Error: %s", logger_prefix, e)
            snippet = _result_snippet(result)
            error_msg = f"{logger_prefix}Parse failed: {e}. LLM output: {snippet}"
            if doc_id:
                error_msg = f"{error_msg} (Doc: {doc_id})"
            append_job_error(job_id, error_msg)
            return [], False

        if enabled_sequence_checking:
            validated_items: List[Any] = []
            for item in items:
                relevant_sequences = getattr(item, "relevant_sequences", None)

                if not isinstance(relevant_sequences, list):
                    logger.debug(
                        "%sItem relevant_sequences is not a list: %s, discarding",
                        logger_prefix,
                        relevant_sequences,
                    )
                    continue

                if not relevant_sequences:
                    logger.debug(
                        "%sNo relevant sequences found for item: %s, discarding",
                        logger_prefix,
                        item,
                    )
                    continue

                valid_sequences: List[Any] = []
                for seq in relevant_sequences:
                    start_sequence = getattr(seq, "start_sequence", None)
                    end_sequence = getattr(seq, "end_sequence", None)

                    if not start_sequence or not end_sequence:
                        logger.debug("%sSequence has invalid data: %s, discarding", logger_prefix, seq)
                        continue

                    if len(start_sequence.strip()) < 10 or len(end_sequence.strip()) < 10:
                        logger.debug(
                            "%sSequence start or end is too short (less than 10 chars), discarding. Sequence: %s",
                            logger_prefix,
                            seq,
                        )
                        continue

                    if len(start_sequence) > 2000 or len(end_sequence) > 2000:
                        logger.debug(
                            "%sSequence start or end is too long (more than 2000 chars), discarding. Sequence: %s",
                            logger_prefix,
                            seq,
                        )
                        continue

                    start_match = _find_best_fuzzy_literal(
                        text, start_sequence, fuzzy_marker_error_ratio=fuzzy_marker_error_ratio
                    )
                    if not start_match:
                        logger.debug("%sSequence start not found in text: %s, discarding", logger_prefix, seq)
                        continue

                    end_match = _find_closest_best_fuzzy_literal(
                        text,
                        end_sequence,
                        start_match.end,
                        fuzzy_marker_error_ratio=fuzzy_marker_error_ratio,
                        max_length=sequence_max_length,
                    )
                    if not end_match:
                        logger.debug("%sSequence end not found after start in text: %s, discarding", logger_prefix, seq)
                        continue

                    matched_text = text[start_match.start : end_match.end]
                    logger.debug(
                        "%sValidating sequence pair. Start: %s, End: %s, Start distance: %d/%d, End distance: %d/%d, full text: %s",
                        logger_prefix,
                        start_sequence,
                        end_sequence,
                        start_match.dist,
                        _calculate_fuzzy_error_budget(start_sequence, fuzzy_marker_error_ratio),
                        end_match.dist,
                        _calculate_fuzzy_error_budget(end_sequence, fuzzy_marker_error_ratio),
                        matched_text,
                    )

                    if not doc_id:
                        logger.warning("%sNo doc_id provided, skipping sequence: %s", logger_prefix, item)
                        continue

                    seq.docUuid = str(doc_id)

                    seq.start_sequence = text[start_match.start : start_match.end]
                    seq.end_sequence = text[end_match.start : end_match.end]

                    logger.debug(
                        "%sNew validated start: %s, end: %s", logger_prefix, seq.start_sequence, seq.end_sequence
                    )

                    valid_sequences.append(seq)

                if not valid_sequences:
                    logger.debug("%sNo valid sequences found for item: %s, discarding", logger_prefix, item)
                    continue

                item.relevant_sequences = valid_sequences
                validated_items.append(item)

            items = validated_items

        has_relevant_data = bool(items)

        # Optional: annotate items with chunk index (for backward compatibility, always 0 now)
        if track_chunk_per_item and items:
            for item in items:
                if hasattr(item, "__dict__"):
                    item._chunk_index = 0

        logger.info(
            "%sExtraction complete. Total items: %d, has_relevant_data=%s",
            logger_prefix,
            len(items),
            has_relevant_data,
        )

        return items, has_relevant_data

    except Exception as e:
        logger.error("%sDocument processing failed. Error: %s", logger_prefix, e)
        error_msg = f"{logger_prefix}Document call failed: {e}"
        if doc_id:
            error_msg = f"{error_msg} (Doc: {doc_id})"
        append_job_error(job_id, error_msg)
        return [], False


async def run_item_build_parallel(
    item: Any,
    pydantic_model: type[T],
    system_prompt: str,
    user_prompt: str,
    job_id: UUID,
    parse_fn: Callable[[T, Any], Any],
    logger_prefix: str = "",
) -> Any:
    """
    Run LLM item building.

    Args:
        item: The item to process (e.g., extracted auth method without full details)
        system_prompt: The system prompt to use for the LLM
        user_prompt: The user prompt template to use for the LLM (should include {item} placeholder)
        job_id: ID of the job for progress tracking
        logger_prefix: Optional prefix for log messages
    Returns:
        Built item
    """

    parser: BaseOutputParser = PydanticOutputParser(pydantic_object=pydantic_model)
    llm = get_default_llm()
    prompt = ChatPromptTemplate.from_messages(
        [("system", system_prompt + "\n\n{format_instructions}"), ("human", user_prompt)]
    ).partial(format_instructions=parser.get_format_instructions())
    chain = make_basic_chain(prompt, llm, parser)

    try:
        logger.info("%sCalling LLM for document extraction.", logger_prefix)

        result = cast(
            T,
            await chain.ainvoke(
                {"item": item},
                config=RunnableConfig(callbacks=[langfuse_handler]),
            ),
        )
        logger.debug("%sLLM result: %r", logger_prefix, (result or ""))

        if not result:
            logger.warning("%sEmpty LLM response.", logger_prefix)
            error_msg = f"{logger_prefix}Empty LLM response."
            append_job_error(job_id, error_msg)
            return [], False

        # Parse structured output
        try:
            new_item = parse_fn(result, item)
        except (ValidationError, ValueError, json.JSONDecodeError) as e:
            logger.info("%sJSON parse failed. Error: %s", logger_prefix, e)
            snippet = _result_snippet(result)
            error_msg = f"{logger_prefix}Parse failed: {e}. LLM output: {snippet}"
            append_job_error(job_id, error_msg)
            return [], False

        return new_item
    except Exception as e:
        logger.error("%sItem building failed. Error: %s", logger_prefix, e)
        error_msg = f"{logger_prefix}Item building call failed: {e}"
        append_job_error(job_id, error_msg)
        return None


async def run_all_items_build_parallel(
    items: List[Any],
    pydantic_model: type[T],
    system_prompt: str,
    user_prompt: str,
    job_id: UUID,
    parse_fn: Callable[[T, Any], Any],
    logger_prefix: str = "",
) -> List[Any]:
    """
    Run LLM item building for all items in parallel.

    Args:
        items: List of items to process (e.g., extracted auth methods without full details)
        system_prompt: The system prompt to use for the LLM
        user_prompt: The user prompt template to use for the LLM (should include {item} placeholder)
        job_id: ID of the job for progress tracking
        logger_prefix: Optional prefix for log messages
    Returns:
        List of built items
    """
    return list(
        await asyncio.gather(
            *(
                run_item_build_parallel(
                    item,
                    pydantic_model,
                    system_prompt,
                    user_prompt,
                    job_id,
                    parse_fn,
                    f"{logger_prefix}Item {idx + 1}/{len(items)} - ",
                )
                for idx, item in enumerate(items)
            )
        )
    )
