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

from pydantic import BaseModel, ValidationError

from src.config import config
from src.core import pool
from src.core.llm import build_structured_chain, raise_if_llm_unavailable, retry_on_transient_llm_error
from src.documents.chunking import normalize_to_text
from src.jobs import append_job_error, update_job_progress
from src.modules.digester.extraction.fuzzysearch_worker import fuzzy_search_worker
from src.modules.digester.extraction.llm_execution import invoke_llm, run_chunks_concurrently
from src.modules.digester.extraction.metadata_helper import extract_summary_and_tags
from src.modules.digester.schemas import DocMarkerMatch, DocProcessingSequenceItem
from src.modules.digester.selection import build_chunk_id_to_doc_id, collect_relevant_chunks
from src.shared.enums import JobStage

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


async def run_doc_extractors_concurrently(
    *,
    chunk_items: List[dict],
    job_id: UUID,
    extractor: Callable[[str, UUID, UUID], Any],
    set_total: bool = True,
):
    """Run a digester extractor over stored documentation chunks."""
    return await run_chunks_concurrently(
        chunk_items=chunk_items,
        job_id=job_id,
        extractor=extractor,
        set_total=set_total,
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
    chunk_id_to_doc_id = build_chunk_id_to_doc_id(chunk_items)

    results = await run_doc_extractors_concurrently(
        chunk_items=chunk_items,
        job_id=job_id,
        extractor=extractor,
    )

    for raw_result, _has_relevant_data, chunk_id in results:
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

    all_relevant_chunks = collect_relevant_chunks(results, chunk_id_to_doc_id, logger_scope)

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
        pool.require_process_pool(),
        fuzzy_search_worker,
        collapsed_text,
        collapsed_marker,
        start_pos,
        max_errors,
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
        pool.require_process_pool(),
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
) -> DocProcessingSequenceItem | None:
    start_sequence = getattr(seq, "start_sequence", None)
    end_sequence = getattr(seq, "end_sequence", None)

    if not isinstance(start_sequence, str) or not isinstance(end_sequence, str):
        logger.info("[Digester:Sequences] Sequence has invalid data: %s, discarding", seq)
        return None

    if not start_sequence.strip() or not end_sequence.strip():
        logger.info("[Digester:Sequences] Sequence has empty start or end data: %s, discarding", seq)
        return None

    if len(start_sequence) < min_start_sequence_length or len(end_sequence) < min_end_sequence_length:
        logger.info(
            "[Digester:Sequences] Sequence start or end is too short (less than %d chars for start and %d for end), discarding. Sequence: %s",
            min_start_sequence_length,
            min_end_sequence_length,
            seq,
        )
        return None

    if len(start_sequence) > max_start_sequence_length or len(end_sequence) > max_end_sequence_length:
        logger.info(
            "[Digester:Sequences] Sequence start or end is too long (more than %d chars for start and %d for end), discarding. Sequence: %s",
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
        logger.info("[Digester:Sequences] Sequence start not found in text: %s, discarding", seq)
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
        logger.info("[Digester:Sequences] Sequence end not found after start in text: %s, discarding", seq)
        return None

    matched_text = text[start_match.start_position : end_match.end_position]
    logger.debug(
        "[Digester:Sequences] Validating sequence pair. Start: %s, End: %s, Start distance: %d/%d, End distance: %d/%d, full text: %s",
        start_sequence,
        end_sequence,
        start_match.distance,
        await _calculate_fuzzy_error_budget(start_sequence, fuzzy_start_marker_error_ratio),
        end_match.distance,
        await _calculate_fuzzy_error_budget(end_sequence, fuzzy_end_marker_error_ratio),
        matched_text,
    )

    if not doc_id:
        logger.warning("[Digester:Sequences] No doc_id provided, skipping sequence: %s", item)
        return None

    validated_seq = DocProcessingSequenceItem(
        chunk_id=str(doc_id),
        start_sequence=text[start_match.start_position : start_match.end_position],
        end_sequence=text[end_match.start_position : end_match.end_position],
        text=matched_text,
    )

    logger.debug(
        "[Digester:Sequences] New validated start: %s, end: %s",
        validated_seq.start_sequence,
        validated_seq.end_sequence,
    )

    return validated_seq


async def _validate_item_relevant_sequences(
    item: Any,
    text: str,
    collapsed_text: str,
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
    validated_item_model: type[BaseModel] | None = None,
) -> Optional[Any]:
    relevant_sequences = getattr(item, "relevant_sequences", None)

    if not isinstance(relevant_sequences, list):
        logger.info(
            "[Digester:Sequences] Item relevant_sequences is not a list: %s, discarding",
            relevant_sequences,
        )
        return None

    if not relevant_sequences:
        logger.info(
            "[Digester:Sequences] No relevant sequences found for item: %s, discarding",
            item,
        )
        return None

    valid_sequences: List[DocProcessingSequenceItem] = []
    for seq in relevant_sequences:
        validated_seq = await _validate_relevant_sequence(
            seq,
            item,
            text,
            collapsed_text,
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
        logger.info("[Digester:Sequences] No valid sequences found for item: %s, discarding", item)
        return None

    if validated_item_model is not None:
        return validated_item_model.model_validate(
            {**item.model_dump(exclude={"relevant_sequences"}), "relevant_sequences": valid_sequences}
        )

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
    return build_structured_chain(system_prompt, user_prompt, pydantic_model)


async def extract_single_chunk(
    schema: str,
    pydantic_model: type[T],
    system_prompt: str,
    user_prompt: str,
    parse_fn: Callable[[T], List[Any]],
    job_id: UUID,
    *,
    run_name: str,
    chunk_id: Optional[UUID] = None,
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
    validated_item_model: type[BaseModel] | None = None,
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
        run_name: Explicit operation name for tracing and job errors
        chunk_id: Optional chunk ID for tracking
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
        validated_item_model: Build this model after sequence validation instead of mutating the raw item.
            Requires enabled_sequence_checking.

    Returns:
        - Flat list of extracted items
        - Boolean indicating if any relevant data was found
    """
    if validated_item_model is not None and not enabled_sequence_checking:
        raise ValueError("validated_item_model requires enabled_sequence_checking")

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
    min_start_sequence_length = (
        min_start_sequence_length
        if min_start_sequence_length is not None
        else digester_config.min_start_sequence_length
    )
    max_start_sequence_length = (
        max_start_sequence_length
        if max_start_sequence_length is not None
        else digester_config.max_start_sequence_length
    )
    min_end_sequence_length = (
        min_end_sequence_length if min_end_sequence_length is not None else digester_config.min_end_sequence_length
    )
    max_end_sequence_length = (
        max_end_sequence_length if max_end_sequence_length is not None else digester_config.max_end_sequence_length
    )

    # Progress: start processing
    await update_job_progress(
        job_id,
        stage=JobStage.processing_chunks,
        message="Processing chunk and extracting relevant information",
    )

    logger.info("[Digester:ChunkExtraction] Calling %s for chunk %s", run_name, chunk_id)
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
            await retry_on_transient_llm_error(
                lambda: invoke_llm(
                    extraction_chain,
                    {"chunk": text, "summary": summary, "tags": tags, **(extra_llm_attrs or {})},
                    run_name=run_name,
                ),
                max_attempts=digester_config.chunk_llm_retry_attempts,
                base_delay=digester_config.chunk_llm_retry_base_delay_seconds,
                context=f"{run_name} for chunk {chunk_id}" if chunk_id else run_name,
            ),
        )
        logger.debug("[Digester:ChunkExtraction] Result from %s: %r", run_name, result or "")

        if not result:
            error_message = f"[{run_name}] Empty LLM response."
            if chunk_id:
                error_message = f"{error_message} (chunk_id: {chunk_id})"
            logger.warning("[Digester:ChunkExtraction] Empty response from %s for chunk %s", run_name, chunk_id)
            await append_job_error(job_id, error_message)
            return [], False

        # Parse structured output
        try:
            items = parse_fn(result)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            snippet = _result_snippet(result)
            error_message = f"[{run_name}] Failed to parse LLM output: {exc}. LLM output: {snippet}"
            if chunk_id:
                error_message = f"{error_message} (chunk_id: {chunk_id})"
            logger.exception(
                "[Digester:ChunkExtraction] Failed to parse %s for chunk %s. LLM output: %s",
                run_name,
                chunk_id,
                snippet,
            )
            await append_job_error(job_id, error_message)
            return [], False

        if enabled_sequence_checking:
            collapsed_text = re.sub(r"\s+", " ", text)
            validated_item_candidates = await asyncio.gather(
                *(
                    _validate_item_relevant_sequences(
                        item,
                        text,
                        collapsed_text,
                        chunk_id,
                        enable_marker_blending,
                        fuzzy_start_marker_error_ratio,
                        fuzzy_end_marker_error_ratio,
                        sequence_max_length,
                        min_start_sequence_length,
                        max_start_sequence_length,
                        min_end_sequence_length,
                        max_end_sequence_length,
                        marker_word_cutoff_length,
                        validated_item_model=validated_item_model,
                    )
                    for item in items
                )
            )

            items = [item for item in validated_item_candidates if item is not None]

        has_relevant_data = bool(items)

        return items, has_relevant_data

    except Exception as exc:
        raise_if_llm_unavailable(exc, context="extracting documentation")
        error_message = f"[{run_name}] Failed to process chunk: {exc}"
        if chunk_id:
            error_message = f"{error_message} (chunk_id: {chunk_id})"
        logger.exception("[Digester:ChunkExtraction] Operation %s failed for chunk %s", run_name, chunk_id)
        await append_job_error(job_id, error_message)
        return [], False


async def run_item_build_parallel(
    item: Any,
    chain: Any,
    job_id: UUID,
    parse_fn: Callable[[T, Any], Any],
    *,
    run_name: str,
    item_index: int,
    item_count: int,
) -> Any | None:
    """
    Run LLM item building.

    Args:
        item: The item to process (e.g., extracted auth method without full details)
        chain: Reusable LLM chain configured for this item build batch
        job_id: ID of the job for progress tracking
        run_name: Explicit operation name for tracing and job errors
        item_index: One-based item index for logs and job errors, never trace names
        item_count: Total number of items in this build batch
    Returns:
        Built item, or None when the item could not be built
    """

    error_context = f"[{run_name}] Item {item_index}/{item_count} - "
    try:
        logger.info("[Digester:BuildItem] Calling %s for item %d/%d", run_name, item_index, item_count)

        result = cast(
            T,
            await invoke_llm(
                chain,
                {"item": item},
                run_name=run_name,
            ),
        )
        logger.debug(
            "[Digester:BuildItem] Result from %s for item %d/%d: %r", run_name, item_index, item_count, result or ""
        )

        if not result:
            logger.warning(
                "[Digester:BuildItem] Empty response from %s for item %d/%d", run_name, item_index, item_count
            )
            error_msg = f"{error_context}Empty LLM response."
            await append_job_error(job_id, error_msg)
            return None

        # Parse structured output
        try:
            new_item = parse_fn(result, item)
        except (ValidationError, ValueError, json.JSONDecodeError) as e:
            logger.info(
                "[Digester:BuildItem] Parsing %s failed for item %d/%d: %s", run_name, item_index, item_count, e
            )
            snippet = _result_snippet(result)
            error_msg = f"{error_context}Parse failed: {e}. LLM output: {snippet}"
            await append_job_error(job_id, error_msg)
            return None

        return new_item
    except Exception as e:
        raise_if_llm_unavailable(e, context="building extracted items")
        logger.exception("[Digester:BuildItem] Operation %s failed for item %d/%d", run_name, item_index, item_count)
        error_msg = f"{error_context}Item building call failed: {e}"
        await append_job_error(job_id, error_msg)
        return None


async def run_all_items_build_parallel(
    items: List[Any],
    pydantic_model: type[T],
    system_prompt: str,
    user_prompt: str,
    job_id: UUID,
    parse_fn: Callable[[T, Any], Any],
    *,
    run_name: str,
) -> List[Any]:
    """
    Run LLM item building for all items in parallel.

    Args:
        items: List of items to process (e.g., extracted auth methods without full details)
        system_prompt: The system prompt to use for the LLM
        user_prompt: The user prompt template to use for the LLM (should include {item} placeholder)
        job_id: ID of the job for progress tracking
        run_name: Explicit operation name for tracing and job errors
    Returns:
        List of built items
    """
    chain = build_structured_chain(system_prompt, user_prompt, pydantic_model)

    return list(
        await asyncio.gather(
            *(
                run_item_build_parallel(
                    item,
                    chain,
                    job_id,
                    parse_fn,
                    run_name=run_name,
                    item_index=idx + 1,
                    item_count=len(items),
                )
                for idx, item in enumerate(items)
            )
        )
    )
