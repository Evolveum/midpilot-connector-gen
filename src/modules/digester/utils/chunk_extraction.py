# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import json
import logging
import math
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar, cast
from uuid import UUID

from langchain_core.output_parsers import BaseOutputParser, PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables.config import RunnableConfig
from pydantic import BaseModel, ValidationError

from src import pool
from src.common.chunks import normalize_to_text
from src.common.enums import JobStage
from src.common.jobs import append_job_error, update_job_progress
from src.common.langfuse import langfuse_handler
from src.common.llm import get_default_llm, make_basic_chain
from src.config import config
from src.modules.digester.utils.fuzzysearch_worker import fuzzy_search_worker
from src.modules.digester.utils.metadata_helper import extract_summary_and_tags

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


async def _calculate_fuzzy_error_budget(marker: str, fuzzy_marker_error_ratio: float) -> int:
    stripped_marker = marker.strip()
    if not stripped_marker:
        return 0
    return max(1, math.floor(len(stripped_marker) * fuzzy_marker_error_ratio))


async def _find_best_fuzzy_literal(
    text: str, marker: str, start_pos: int = 0, fuzzy_marker_error_ratio: float = 0.05
) -> Optional[Any]:
    if not marker.strip() or start_pos >= len(text):
        return None

    max_errors = await _calculate_fuzzy_error_budget(marker, fuzzy_marker_error_ratio)

    print(f"[fuzzy] pool.process_pool = {pool.process_pool}")
    matches = await asyncio.get_event_loop().run_in_executor(
        pool.process_pool, fuzzy_search_worker, text, marker, start_pos, max_errors
    )
    if not matches:
        return None

    return min(matches, key=lambda match: (match.dist, match.start))


async def _find_closest_best_fuzzy_literal(
    text: str, marker: str, start_pos: int, fuzzy_marker_error_ratio: float = 0.05, max_length: int = 10000
) -> Optional[Any]:
    if not marker.strip() or start_pos >= len(text):
        return None

    max_errors = await _calculate_fuzzy_error_budget(marker, fuzzy_marker_error_ratio)

    matches = await asyncio.get_event_loop().run_in_executor(
        pool.process_pool, fuzzy_search_worker, text, marker, start_pos, max_errors, start_pos + max_length
    )

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


async def _validate_relevant_sequence(
    seq: Any,
    item: Any,
    text: str,
    logger_prefix: str,
    doc_id: Optional[UUID],
    enable_marker_blending: bool,
    fuzzy_marker_error_ratio: float,
    sequence_max_length: int,
) -> Optional[Any]:
    start_sequence = getattr(seq, "start_sequence", None)
    end_sequence = getattr(seq, "end_sequence", None)

    if not start_sequence or not end_sequence:
        logger.info("%sSequence has invalid data: %s, discarding", logger_prefix, seq)
        return None

    if len(start_sequence.strip()) < 10 or len(end_sequence.strip()) < 10:
        logger.info(
            "%sSequence start or end is too short (less than 10 chars), discarding. Sequence: %s",
            logger_prefix,
            seq,
        )
        return None

    if len(start_sequence) > 2000 or len(end_sequence) > 2000:
        logger.info(
            "%sSequence start or end is too long (more than 2000 chars), discarding. Sequence: %s",
            logger_prefix,
            seq,
        )
        return None

    start_match = await _find_best_fuzzy_literal(
        text, start_sequence, fuzzy_marker_error_ratio=fuzzy_marker_error_ratio
    )
    if not start_match:
        logger.info("%sSequence start not found in text: %s, discarding", logger_prefix, seq)
        return None

    end_match = await _find_closest_best_fuzzy_literal(
        text,
        end_sequence,
        start_match.start if enable_marker_blending else start_match.end,
        fuzzy_marker_error_ratio=fuzzy_marker_error_ratio,
        max_length=sequence_max_length,
    )
    if not end_match:
        logger.info("%sSequence end not found after start in text: %s, discarding", logger_prefix, seq)
        return None

    matched_text = text[start_match.start : end_match.end]
    logger.debug(
        "%sValidating sequence pair. Start: %s, End: %s, Start distance: %d/%d, End distance: %d/%d, full text: %s",
        logger_prefix,
        start_sequence,
        end_sequence,
        start_match.dist,
        await _calculate_fuzzy_error_budget(start_sequence, fuzzy_marker_error_ratio),
        end_match.dist,
        await _calculate_fuzzy_error_budget(end_sequence, fuzzy_marker_error_ratio),
        matched_text,
    )

    if not doc_id:
        logger.warning("%sNo doc_id provided, skipping sequence: %s", logger_prefix, item)
        return None

    seq.chunk_id = str(doc_id)
    seq.start_sequence = text[start_match.start : start_match.end]
    seq.end_sequence = text[end_match.start : end_match.end]

    logger.info("%sNew validated start: %s, end: %s", logger_prefix, seq.start_sequence, seq.end_sequence)

    return seq


async def _validate_item_relevant_sequences(
    item: Any,
    text: str,
    logger_prefix: str,
    doc_id: Optional[UUID],
    enable_marker_blending: bool,
    fuzzy_marker_error_ratio: float,
    sequence_max_length: int,
) -> Optional[Any]:
    relevant_sequences = getattr(item, "relevant_sequences", None)

    if not isinstance(relevant_sequences, list):
        logger.info(
            "%sItem relevant_sequences is not a list: %s, discarding",
            logger_prefix,
            relevant_sequences,
        )
        return None

    if not relevant_sequences:
        logger.info(
            "%sNo relevant sequences found for item: %s, discarding",
            logger_prefix,
            item,
        )
        return None

    valid_sequences: List[Any] = []
    for seq in relevant_sequences:
        validated_seq = await _validate_relevant_sequence(
            seq,
            item,
            text,
            logger_prefix,
            doc_id,
            enable_marker_blending,
            fuzzy_marker_error_ratio,
            sequence_max_length,
        )
        if validated_seq is not None:
            valid_sequences.append(validated_seq)

    if not valid_sequences:
        logger.info("%sNo valid sequences found for item: %s, discarding", logger_prefix, item)
        return None

    item.relevant_sequences = valid_sequences
    return item


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


def build_chunk_extraction_chain(
    *,
    pydantic_model: type[T],
    system_prompt: str,
    user_prompt: str,
) -> Any:
    """Build a reusable structured extraction chain for repeated chunk invocations."""
    parser: BaseOutputParser = PydanticOutputParser(pydantic_object=pydantic_model)
    llm = get_default_llm()
    prompt = ChatPromptTemplate.from_messages(
        [("system", system_prompt + "\n\n{format_instructions}"), ("human", user_prompt)]
    ).partial(format_instructions=parser.get_format_instructions())
    return make_basic_chain(prompt, llm, parser)


async def extract_single_chunk(
    schema: str,
    pydantic_model: type[T],
    system_prompt: str,
    user_prompt: str,
    parse_fn: Callable[[T], List[Any]],
    job_id: UUID,
    logger_prefix: str = "",
    chunk_id: Optional[UUID] = None,
    track_chunk_per_item: bool = False,
    chunk_metadata: Optional[Dict[str, Any]] = None,
    enabled_sequence_checking: bool = False,
    enable_marker_blending: bool = False,
    fuzzy_marker_error_ratio: Optional[float] = None,
    sequence_max_length: Optional[int] = None,
    extra_llm_attrs: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Any], bool]:
    """
    Run LLM extraction on a pre-chunked documentation item.

    Since inputs are already pre-chunked in the DB (max 20000 tokens),
    no further splitting is needed - we process the chunk as-is.

    Args:
        schema: The pre-chunked text to be processed
        pydantic_model: The Pydantic model to validate the extracted data against
        system_prompt: The system prompt to use for the LLM
        user_prompt: The user prompt template to use for the LLM
        parse_fn: Function to parse the Pydantic model into a list of items
        job_id: ID of the job for progress tracking
        logger_prefix: Optional prefix for log messages
        chunk_id: Optional chunk ID for tracking
        track_chunk_per_item: Deprecated (kept for backward compatibility, always sets index to 0)
        chunk_metadata: Optional metadata about the chunk (summary, tags, etc.)
        enabled_sequence_checking: Whether to enable checking if the start and end of the sequence are valid (only once present in a chunk)
        enable_marker_blending: Whether to enable marker blending - end and start markers can use the same text
    Returns:
        - Flat list of extracted items
        - Boolean indicating if any relevant data was found
    """
    # Normalize text (input is already a single pre-chunked unit)
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
        message="Processing chunk and extracting relevant information",
    )

    logger.info("%sLLM call for chunk %s", logger_prefix, chunk_id)
    extraction_chain = build_chunk_extraction_chain(
        pydantic_model=pydantic_model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )

    # Process the chunk (already pre-chunked)
    try:
        # Extract summary and tags from chunk metadata
        summary, tags = extract_summary_and_tags(chunk_metadata)

        result = cast(
            T,
            await extraction_chain.ainvoke(
                {"chunk": text, "summary": summary, "tags": tags, **(extra_llm_attrs or {})},
                config=RunnableConfig(callbacks=[langfuse_handler]),
            ),
        )
        logger.debug("%sLLM result: %r", logger_prefix, (result or ""))

        if not result:
            error_message = f"{logger_prefix}Empty LLM response."
            if chunk_id:
                error_message = f"{error_message} (chunk_id: {chunk_id})"
            logger.warning(error_message)
            append_job_error(job_id, error_message)
            return [], False

        # Parse structured output
        try:
            items = parse_fn(result)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            snippet = _result_snippet(result)
            error_message = f"{logger_prefix}Failed to parse LLM output: {exc}. LLM output: {snippet}"
            if chunk_id:
                error_message = f"{error_message} (chunk_id: {chunk_id})"
            logger.exception(error_message)
            append_job_error(job_id, error_message)
            return [], False

        if enabled_sequence_checking:
            validated_item_candidates = await asyncio.gather(
                *(
                    _validate_item_relevant_sequences(
                        item,
                        text,
                        logger_prefix,
                        chunk_id,
                        enable_marker_blending,
                        fuzzy_marker_error_ratio,
                        sequence_max_length,
                    )
                    for item in items
                )
            )

            items = [item for item in validated_item_candidates if item is not None]

        has_relevant_data = bool(items)

        if track_chunk_per_item and items:
            for item in items:
                if hasattr(item, "__dict__"):
                    item._chunk_index = 0

        return items, has_relevant_data

    except Exception as exc:
        error_message = f"{logger_prefix}Failed to process chunk: {exc}"
        if chunk_id:
            error_message = f"{error_message} (chunk_id: {chunk_id})"
        logger.exception(error_message)
        append_job_error(job_id, error_message)
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
