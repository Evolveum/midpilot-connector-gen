# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import json
import logging
import math
import re
import time
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
from src.modules.digester.schemas import DocMarkerMatch, DocSequenceItem
from src.modules.digester.utils.doc_chunk import build_chunk_id_to_doc_id
from src.modules.digester.utils.fuzzysearch_worker import fuzzy_search_worker
from src.modules.digester.utils.llm_execution import invoke_llm, run_chunks_concurrently
from src.modules.digester.utils.metadata_helper import extract_summary_and_tags

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
TRANSIENT_LLM_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}


def _is_transient_llm_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code in TRANSIENT_LLM_STATUS_CODES:
        return True

    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "504 gateway time-out",
            "504 gateway timeout",
            "gateway time-out",
            "gateway timeout",
            "temporarily unavailable",
            "rate limit",
            "timed out",
            "timeout",
        )
    )


async def _invoke_extraction_chain_with_retry(
    extraction_chain: Any,
    payload: Dict[str, Any],
    *,
    logger_prefix: str,
    chunk_id: Optional[UUID],
) -> Any:
    max_attempts = max(1, config.digester.chunk_llm_retry_attempts)
    base_delay = max(0.0, config.digester.chunk_llm_retry_base_delay_seconds)

    for attempt in range(1, max_attempts + 1):
        try:
            return await invoke_llm(
                extraction_chain,
                payload,
                config=RunnableConfig(callbacks=[langfuse_handler]),
            )
        except Exception as exc:
            if attempt >= max_attempts or not _is_transient_llm_error(exc):
                raise

            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "%sTransient LLM chunk failure for chunk %s; retrying attempt %s/%s in %.1fs: %s",
                logger_prefix,
                chunk_id,
                attempt + 1,
                max_attempts,
                delay,
                exc,
            )
            if delay:
                await asyncio.sleep(delay)

    raise RuntimeError("LLM extraction retry loop exited unexpectedly")


async def run_doc_extractors_concurrently(
    *,
    chunk_items: List[dict],
    job_id: UUID,
    extractor: Callable[[str, UUID, UUID], Any],
    logger_scope: str,
):
    """Run a digester extractor over stored documentation chunks."""
    return await run_chunks_concurrently(
        chunk_items=chunk_items,
        job_id=job_id,
        extractor=extractor,
        logger_scope=logger_scope,
    )


async def process_over_chunks(
    *,
    chunk_items: List[dict],
    job_id: UUID,
    extractor: Callable[[str, UUID, UUID], Any],
    merger: Callable[[List[Dict[str, Any]]], Dict[str, Any]],
    logger_scope: str,
    per_chunk_count: Callable[[Dict[str, Any]], int] | None = None,
) -> Dict[str, Any]:
    """
    Process chunks in parallel, collect relevant chunk references, merge results, and return a digester payload.
    """
    all_results: List[Dict[str, Any]] = []
    all_relevant_chunks: List[Dict[str, Any]] = []
    chunk_id_to_doc_id = build_chunk_id_to_doc_id(chunk_items)

    results = await run_doc_extractors_concurrently(
        chunk_items=chunk_items,
        job_id=job_id,
        extractor=extractor,
        logger_scope=logger_scope,
    )

    for raw_result, has_relevant_data, chunk_id in results:
        if hasattr(raw_result, "model_dump"):
            result_data = cast(Dict[str, Any], raw_result.model_dump(by_alias=True))
        else:
            result_data = cast(Dict[str, Any], raw_result or {})

        if per_chunk_count is not None:
            try:
                count = per_chunk_count(result_data)
            except Exception:
                count = 0
            logger.info("[%s] Chunk %s: extracted %s items", logger_scope, chunk_id, count)

        if result_data:
            all_results.append(result_data)
        if has_relevant_data:
            chunk_id_str = str(chunk_id)
            doc_id = chunk_id_to_doc_id.get(chunk_id_str)
            if doc_id:
                all_relevant_chunks.append({"doc_id": doc_id, "chunk_id": chunk_id_str})
            else:
                logger.warning(
                    "[%s] Missing docId for chunk %s, skipping relevant chunk mapping",
                    logger_scope,
                    chunk_id_str,
                )

    merged_result: Dict[str, Any] = merger(all_results)

    return {
        "result": merged_result,
        "relevantDocumentations": all_relevant_chunks,
    }


async def _calculate_fuzzy_error_budget(marker: str, fuzzy_marker_error_ratio: float) -> int:
    stripped_marker = marker.strip()
    if not stripped_marker:
        return 0
    return max(1, math.floor(len(stripped_marker) * fuzzy_marker_error_ratio))


async def _find_best_fuzzy_literal(
    text: str,
    collapsed_text: str,
    marker: str,
    start_pos: int = 0,
    fuzzy_marker_error_ratio: float = 0.05,
    marker_word_cutoff_length: int = 20,
) -> Optional[DocMarkerMatch]:
    """Find the best fuzzy match for the marker in the text starting from start_pos.
    Runs two passes:
    1) First pass with a fuzzysearch library and collapsed whitespace characters into a single space
    2) Regular regex on correct substring using regex to find the correct position of the match in the original text
    BE AWARE: start pos is definitely not exact, since we do fuzzy matching and also collapse whitespace in the first pass and the same exact start_pos is used in non collapsed and collapsed search.
    """
    if not marker.strip() or start_pos >= len(text):
        return None

    collapsed_marker = re.sub(r"\s+", " ", marker)

    max_errors = await _calculate_fuzzy_error_budget(collapsed_marker, fuzzy_marker_error_ratio)

    started = time.time()
    logger.debug(
        "Starting fuzzy search for marker '%s' with error budget %d at collapsed position %d. Marker word cutoff length: %d",
        marker,
        max_errors,
        start_pos,
        marker_word_cutoff_length,
    )

    matches = await asyncio.get_event_loop().run_in_executor(
        pool.process_pool, fuzzy_search_worker, collapsed_text, collapsed_marker, start_pos, max_errors
    )
    duration = time.time() - started
    if duration > 2:
        logger.warning(
            "Fuzzy search for marker '%s' took a long time: %.2f seconds. Collapsed marker: '%s', start_pos: %d, error budget: %d, marker word cutoff length: %d",
            marker,
            duration,
            collapsed_marker,
            start_pos,
            max_errors,
            marker_word_cutoff_length,
        )

    if not matches:
        return None
    best_match = min(matches, key=lambda match: (match.dist, match.start))

    words = collapsed_text[best_match.start : best_match.end].split(" ")[:marker_word_cutoff_length]
    regex_pattern = r"\s++".join(re.escape(word) for word in words if word.strip())
    search_pattern = re.compile(regex_pattern, re.MULTILINE)
    exact_match = search_pattern.search(text, pos=start_pos)
    if exact_match:
        return DocMarkerMatch(
            start_position=exact_match.start(),
            end_position=exact_match.end(),
            start_position_collapsed=best_match.start,
            end_position_collapsed=best_match.end,
            distance=best_match.dist,
        )
    else:
        logger.warning(
            "Fuzzy match found for marker '%s' with distance %d, but failed to find exact match in original text. Collapsed match: '%s', original text snippet: '%s', compiled regex pattern: '%s'",
            marker,
            best_match.dist,
            collapsed_text[best_match.start : best_match.end],
            text[start_pos : start_pos + 1000],
            regex_pattern,
        )
        return None


async def _find_closest_best_fuzzy_literal(
    text: str,
    collapsed_text: str,
    marker: str,
    start_marker: DocMarkerMatch,
    enable_marker_blending: bool,
    fuzzy_marker_error_ratio: float = 0.05,
    max_length: int = 10000,
    marker_word_cutoff_length: int = 20,
) -> Optional[DocMarkerMatch]:
    """Find the ending sequence match in the text, starting from the position of the starting sequence match.
    Runs two passes:
    1) First pass with a fuzzysearch library and collapsed whitespace characters into a single space
    2) Regular regex on correct substring using regex to find the correct position of the match in the original text

    Big difference between this and starter sequence matching is that we possible don't want the best match in the whole text, but also the closest one."""
    if not marker.strip() or start_marker.start_position >= len(text):
        return None

    collapsed_marker = re.sub(r"\s+", " ", marker)  # (?:\s+|\\n|\\t|\\s)+

    max_errors = await _calculate_fuzzy_error_budget(collapsed_marker, fuzzy_marker_error_ratio)

    started = time.time()
    logger.debug(
        "Starting fuzzy search for end marker '%s' with error budget %d. Start marker collapsed positions: %d-%d. Enable marker blending: %s",
        marker,
        max_errors,
        start_marker.start_position_collapsed,
        start_marker.end_position_collapsed,
        enable_marker_blending,
    )

    matches = await asyncio.get_event_loop().run_in_executor(
        pool.process_pool,
        fuzzy_search_worker,
        collapsed_text,
        collapsed_marker,
        start_marker.start_position_collapsed if enable_marker_blending else start_marker.end_position_collapsed,
        max_errors,
        start_marker.start_position_collapsed + max_length
        if enable_marker_blending
        else start_marker.end_position_collapsed + max_length,
    )
    duration = time.time() - started
    if duration > 2:
        logger.warning(
            "Finished fuzzy search for end marker '%s' in time: %.2f seconds. Matches found: %d. Start marker collapsed positions: %d-%d. Enable marker blending: %s",
            marker,
            duration,
            len(matches),
            start_marker.start_position_collapsed,
            start_marker.end_position_collapsed,
            enable_marker_blending,
        )

    if not matches:
        return None

    # Selection rule:
    # 1) lowest Levenshtein distance
    # 2) if fuzziness ties, choose the closest one after start_pos
    best_match = min(
        matches,
        key=lambda match: (
            match.dist,
            max(0, match.start - start_marker.start_position_collapsed),
        ),
    )

    words = collapsed_text[best_match.start : best_match.end].split(" ")[:marker_word_cutoff_length]
    regex_pattern = r"\s++".join(re.escape(word) for word in words if word.strip())
    compiled_pattern = re.compile(regex_pattern, re.MULTILINE)
    exact_match = compiled_pattern.search(
        text,
        pos=start_marker.start_position if enable_marker_blending else start_marker.end_position,
        endpos=start_marker.start_position + max_length
        if enable_marker_blending
        else start_marker.end_position + max_length,
    )
    if exact_match:
        return DocMarkerMatch(
            start_position=exact_match.start(),
            end_position=exact_match.end(),
            start_position_collapsed=best_match.start,
            end_position_collapsed=best_match.end,
            distance=best_match.dist,
        )
    else:
        logger.warning(
            "Fuzzy match found for marker '%s' with distance %d, but failed to find exact match in original text. Collapsed match: '%s', original text snippet: '%s', compiled regex pattern: '%s'",
            marker,
            best_match.dist,
            collapsed_text[best_match.start : best_match.end],
            text[start_marker.start_position : start_marker.start_position + 1000]
            if enable_marker_blending
            else text[start_marker.end_position : start_marker.end_position + 1000],
            regex_pattern,
        )
        return None


async def _validate_relevant_sequence(
    seq: Any,
    item: Any,
    text: str,
    collapsed_text: str,
    logger_prefix: str,
    doc_id: Optional[UUID],
    enable_marker_blending: bool,
    fuzzy_start_marker_error_ratio: float,
    fuzzy_end_marker_error_ratio: float,
    sequence_max_length: int,
    min_start_sequence_length: int,
    max_start_sequence_length: int,
    min_end_sequence_length: int,
    max_end_sequence_length: int,
    marker_word_cutoff_length: int,
) -> Optional[Any]:
    start_sequence = getattr(seq, "start_sequence", None)
    end_sequence = getattr(seq, "end_sequence", None)

    if not start_sequence or not end_sequence:
        logger.info("%sSequence has invalid data: %s, discarding", logger_prefix, seq)
        return None

    if len(start_sequence) < min_start_sequence_length or len(end_sequence) < min_end_sequence_length:
        logger.info(
            "%sSequence start or end is too short (less than %d chars for start and %d for end), discarding. Sequence: %s",
            logger_prefix,
            min_start_sequence_length,
            min_end_sequence_length,
            seq,
        )
        return None

    if len(start_sequence) > max_start_sequence_length or len(end_sequence) > max_end_sequence_length:
        logger.info(
            "%sSequence start or end is too long (more than %d chars for start and %d for end), discarding. Sequence: %s",
            logger_prefix,
            max_start_sequence_length,
            max_end_sequence_length,
            seq,
        )
        return None

    start_match: DocMarkerMatch | None = await _find_best_fuzzy_literal(
        text,
        collapsed_text,
        start_sequence,
        fuzzy_marker_error_ratio=fuzzy_start_marker_error_ratio,
        marker_word_cutoff_length=marker_word_cutoff_length,
    )
    if not start_match:
        logger.info("%sSequence start not found in text: %s, discarding", logger_prefix, seq)
        return None

    end_match: DocMarkerMatch | None = await _find_closest_best_fuzzy_literal(
        text,
        collapsed_text,
        end_sequence,
        start_marker=start_match,
        enable_marker_blending=enable_marker_blending,
        fuzzy_marker_error_ratio=fuzzy_end_marker_error_ratio,
        max_length=sequence_max_length,
        marker_word_cutoff_length=marker_word_cutoff_length,
    )
    if not end_match:
        logger.info("%sSequence end not found after start in text: %s, discarding", logger_prefix, seq)
        return None

    matched_text = text[start_match.start_position : end_match.end_position]
    logger.debug(
        "%sValidating sequence pair. Start: %s, End: %s, Start distance: %d/%d, End distance: %d/%d, full text: %s",
        logger_prefix,
        start_sequence,
        end_sequence,
        start_match.distance,
        await _calculate_fuzzy_error_budget(start_sequence, fuzzy_start_marker_error_ratio),
        end_match.distance,
        await _calculate_fuzzy_error_budget(end_sequence, fuzzy_end_marker_error_ratio),
        matched_text,
    )

    if not doc_id:
        logger.warning("%sNo doc_id provided, skipping sequence: %s", logger_prefix, item)
        return None

    validated_seq = DocSequenceItem(
        chunk_id=str(doc_id),
        start_sequence=text[start_match.start_position : start_match.end_position],
        end_sequence=text[end_match.start_position : end_match.end_position],
    )

    logger.debug(
        "%sNew validated start: %s, end: %s",
        logger_prefix,
        validated_seq.start_sequence,
        validated_seq.end_sequence,
    )

    return validated_seq


async def _validate_item_relevant_sequences(
    item: Any,
    text: str,
    collapsed_text: str,
    logger_prefix: str,
    doc_id: Optional[UUID],
    enable_marker_blending: bool,
    fuzzy_start_marker_error_ratio: float,
    fuzzy_end_marker_error_ratio: float,
    sequence_max_length: int,
    min_start_sequence_length: int,
    max_start_sequence_length: int,
    min_end_sequence_length: int,
    max_end_sequence_length: int,
    marker_word_cutoff_length: int,
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
            collapsed_text,
            logger_prefix,
            doc_id,
            enable_marker_blending,
            fuzzy_start_marker_error_ratio,
            fuzzy_end_marker_error_ratio,
            sequence_max_length,
            min_start_sequence_length,
            max_start_sequence_length,
            min_end_sequence_length,
            max_end_sequence_length,
            marker_word_cutoff_length,
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
    fuzzy_start_marker_error_ratio: Optional[float] = None,
    fuzzy_end_marker_error_ratio: Optional[float] = None,
    sequence_max_length: Optional[int] = None,
    extra_llm_attrs: Optional[Dict[str, Any]] = None,
    min_start_sequence_length: Optional[int] = None,
    max_start_sequence_length: Optional[int] = None,
    min_end_sequence_length: Optional[int] = None,
    max_end_sequence_length: Optional[int] = None,
    marker_word_cutoff_length: Optional[int] = None,
    extraction_chain: Any | None = None,
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
        fuzzy_start_marker_error_ratio: Optional override for fuzzy error ratio for start sequence validation
        fuzzy_end_marker_error_ratio: Optional override for fuzzy error ratio for end sequence validation
        enabled_sequence_checking: Whether to enable checking if the start and end of the sequence are valid (only once present in a chunk)
        enable_marker_blending: Whether to enable marker blending - end and start markers can use the same text
        min_start_sequence_length: Minimum length for the start sequence
        max_start_sequence_length: Maximum length for the start sequence
        min_end_sequence_length: Minimum length for the end sequence
        max_end_sequence_length: Maximum length for the end sequence
        marker_word_cutoff_length: Maximum length of individual words in sequence markers; longer words are truncated to this length to improve performance
        extraction_chain: Optional pre-built reusable extraction chain. When not provided, one is built from prompts.

    Returns:
        - Flat list of extracted items
        - Boolean indicating if any relevant data was found
    """
    # Normalize text (input is already a single pre-chunked unit)
    text = normalize_to_text(schema)
    digester_config = config.digester
    fuzzy_start_marker_error_ratio = (
        fuzzy_start_marker_error_ratio
        if fuzzy_start_marker_error_ratio is not None
        else digester_config.fuzzy_start_marker_error_ratio
    )
    fuzzy_end_marker_error_ratio = (
        fuzzy_end_marker_error_ratio
        if fuzzy_end_marker_error_ratio is not None
        else digester_config.fuzzy_end_marker_error_ratio
    )
    sequence_max_length = (
        sequence_max_length if sequence_max_length is not None else digester_config.sequence_max_length
    )
    marker_word_cutoff_length = (
        marker_word_cutoff_length
        if marker_word_cutoff_length is not None
        else digester_config.marker_word_cutoff_length
    )

    # Progress: start processing
    await update_job_progress(
        job_id,
        stage=JobStage.processing_chunks,
        message="Processing chunk and extracting relevant information",
    )

    logger.info("%sLLM call for chunk %s", logger_prefix, chunk_id)
    if extraction_chain is None:
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
            await _invoke_extraction_chain_with_retry(
                extraction_chain,
                {"chunk": text, "summary": summary, "tags": tags, **(extra_llm_attrs or {})},
                logger_prefix=logger_prefix,
                chunk_id=chunk_id,
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
            collapsed_text = re.sub(r"\s+", " ", text)
            validated_item_candidates = await asyncio.gather(
                *(
                    _validate_item_relevant_sequences(
                        item,
                        text,
                        collapsed_text,
                        logger_prefix,
                        chunk_id,
                        enable_marker_blending,
                        fuzzy_start_marker_error_ratio,
                        fuzzy_end_marker_error_ratio,
                        sequence_max_length,
                        min_start_sequence_length if min_start_sequence_length is not None else 10,
                        max_start_sequence_length if max_start_sequence_length is not None else 2000,
                        min_end_sequence_length if min_end_sequence_length is not None else 10,
                        max_end_sequence_length if max_end_sequence_length is not None else 2000,
                        marker_word_cutoff_length,
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
            await invoke_llm(
                chain,
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
