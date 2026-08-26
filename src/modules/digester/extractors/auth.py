# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
import logging
from collections import Counter
from typing import Any, Dict, List, Optional, Set, Tuple, cast
from uuid import UUID

from langchain_core.runnables.config import RunnableConfig

from src.config import config
from src.core.llm import build_structured_chain
from src.core.observability.langfuse import langfuse_handler
from src.jobs import append_job_error, update_job_progress
from src.modules.digester.aggregation.sequence_merge import merge_relevant_sequences
from src.modules.digester.extraction.chunk_extraction import (
    extract_single_chunk,
    run_all_items_build_parallel,
    run_doc_extractors_concurrently,
)
from src.modules.digester.extraction.llm_execution import invoke_llm
from src.modules.digester.extraction.metadata_helper import build_doc_metadata_map
from src.modules.digester.extraction.sequences import extract_sequence
from src.modules.digester.prompts.auth_prompts import (
    auth_build_system_prompt,
    auth_build_user_prompt,
    auth_deduplication_system_prompt,
    auth_deduplication_user_prompt,
    get_auth_discovery_system_prompt,
    get_auth_discovery_user_prompt,
)
from src.modules.digester.prompts.rest.sorting_output_prompts import sort_auth_system_prompt, sort_auth_user_prompt
from src.modules.digester.schemas import (
    AuthBuildResponse,
    AuthDedupResponse,
    AuthInfo,
    AuthProcessingInfo,
    AuthResponse,
    DiscoveryAuth,
    DocProcessingSequenceItem,
    DocSequenceItem,
)
from src.modules.digester.selection import build_chunk_id_to_doc_id, collect_relevant_chunks
from src.shared.auth import auth_match_key
from src.shared.enums import JobStage

logger = logging.getLogger(__name__)


def _order_dedup_pairs(
    dedup_pairs: List[Tuple[Tuple[str, str], Tuple[str, str]]],
) -> List[Tuple[Tuple[str, str], Tuple[str, str]]]:
    """Topologically order dedup pairs so transitive merges are applied safely.

    If pair A deletes an entry that pair B keeps, B must run before A.
    """

    def _norm_key(item: Tuple[str, str]) -> Tuple[str, str]:
        return auth_match_key(item[0], item[1])

    indexed_pairs = list(enumerate(dedup_pairs))
    keep_keys = {idx: _norm_key(pair[0]) for idx, pair in indexed_pairs}
    delete_keys = {idx: _norm_key(pair[1]) for idx, pair in indexed_pairs}

    # edge j -> i means j must be processed before i
    deps: Dict[int, Set[int]] = {idx: set() for idx, _ in indexed_pairs}
    indegree: Dict[int, int] = {idx: 0 for idx, _ in indexed_pairs}

    for i, _ in indexed_pairs:
        for j, _ in indexed_pairs:
            if i == j:
                continue
            if delete_keys[i] == keep_keys[j]:
                if i not in deps[j]:
                    deps[j].add(i)
                    indegree[i] += 1

    queue: List[int] = [idx for idx, _ in indexed_pairs if indegree[idx] == 0]
    ordered_indices: List[int] = []

    while queue:
        idx = queue.pop(0)
        ordered_indices.append(idx)
        for nxt in deps[idx]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)

    if len(ordered_indices) != len(dedup_pairs):
        logger.warning("[Digester:Auth] Cyclic dedup pair dependencies detected; using original pair order.")
        return dedup_pairs

    return [dedup_pairs[idx] for idx in ordered_indices]


async def extract_auth_raw(
    schema: str, job_id: UUID, chunk_id: Optional[UUID] = None, chunk_metadata: Optional[Dict] = None
) -> Tuple[List[DiscoveryAuth], bool]:
    """
    Extract raw auth info from a single chunk with one LLM call.
    Does NOT deduplicate or sort - that's done later across all chunks.

    Returns:
        - List of raw DiscoveryAuth instances
        - Boolean indicating if relevant data was found
    """

    def parse_fn(result: AuthResponse[DiscoveryAuth]) -> List[DiscoveryAuth]:
        return result.auth or []

    extracted, has_relevant_data = await extract_single_chunk(
        schema=schema,
        pydantic_model=AuthResponse[DiscoveryAuth],
        system_prompt=get_auth_discovery_system_prompt,
        user_prompt=get_auth_discovery_user_prompt,
        parse_fn=parse_fn,
        logger_prefix="[Digester:Auth] ",
        job_id=job_id,
        chunk_id=chunk_id,
        chunk_metadata=chunk_metadata,
        enabled_sequence_checking=True,
        min_start_sequence_length=config.digester.min_start_sequence_len_auth,
        max_start_sequence_length=config.digester.max_start_sequence_len_auth,
        min_end_sequence_length=config.digester.min_end_sequence_len_auth,
        max_end_sequence_length=config.digester.max_end_sequence_len_auth,
    )

    logger.info("[Digester:Auth] Discovery extraction complete from document. Count: %d", len(extracted))
    return extracted, has_relevant_data


async def build_auth_items(auth_info: List[AuthProcessingInfo], job_id: UUID) -> List[AuthProcessingInfo]:
    """
    Build auth items with per-item parallel LLM calls.

    Returns:
        List of AuthProcessingInfo with built details (e.g., filled quirks, standardized types)
    """

    def parse_fn(result: AuthBuildResponse, original: AuthProcessingInfo) -> AuthProcessingInfo:
        name = result.name if result.name else original.name
        fin_type = result.type if result.type else original.type
        quirks = result.quirks if result.quirks else original.quirks
        return AuthProcessingInfo(
            name=name,
            type=fin_type,
            quirks=quirks,
            relevant_sequences=original.relevant_sequences,
        )

    await update_job_progress(job_id, stage=JobStage.building, message="Building normalized auth items")

    try:
        built_items = await run_all_items_build_parallel(
            items=auth_info,
            pydantic_model=AuthBuildResponse,
            system_prompt=auth_build_system_prompt,
            user_prompt=auth_build_user_prompt,
            parse_fn=parse_fn,
            logger_prefix="[Digester:Auth:Build] ",
            job_id=job_id,
        )

        built_auth_items = [item for item in built_items if isinstance(item, AuthProcessingInfo)]
        skipped_items = len(built_items) - len(built_auth_items)
        if skipped_items:
            logger.warning("[Digester:Auth] Dropped %d failed auth build result(s).", skipped_items)

        logger.info("[Digester:Auth] Building complete. Built items count: %d", len(built_auth_items))
        await update_job_progress(job_id, stage=JobStage.building_finished, message="Auth item building finished")
        return built_auth_items
    except Exception as e:
        await update_job_progress(job_id, stage=JobStage.building_failed, message=f"Auth item building failed: {e}")
        await append_job_error(job_id, f"[Digester:Auth] Building failed: {e}")
        return []


def _merge_quirks(target: AuthProcessingInfo, source: AuthProcessingInfo) -> None:
    """Append the source quirks onto the target unless they are already contained."""
    target_quirks = target.quirks.strip() if target.quirks else ""
    source_quirks = source.quirks.strip() if source.quirks else ""

    if not source_quirks:
        return
    if not target_quirks:
        target.quirks = source_quirks
        return

    target_quirks_norm = target_quirks.lower()
    source_quirks_norm = source_quirks.lower()
    if source_quirks_norm in target_quirks_norm or target_quirks_norm in source_quirks_norm:
        return

    target.quirks = f"{target_quirks}\n\n{source_quirks}"


def _heuristic_dedup(
    auth_info: List[DiscoveryAuth] | List[AuthProcessingInfo],
) -> List[DiscoveryAuth | AuthProcessingInfo]:
    """Collapse name/type duplicates, merging their relevant sequences (and quirks).

    Matches an entry against every already-seen key three ways: exact match, the seen
    name being a substring of the new one (the seen entry is superseded and dropped),
    or the new name being a substring of a seen one (the new entry is folded in).
    """
    seen: Dict[Tuple[str, str], DiscoveryAuth | AuthProcessingInfo] = {}

    for auth in auth_info:
        if not auth or not auth.name:
            continue
        name_norm, type_norm = auth_match_key(auth.name, auth.type)
        key = (name_norm, type_norm)

        is_duplicate = False
        delete_from_seen: Optional[Tuple[str, str]] = None
        for seen_key in seen:
            seen_name, seen_type = seen_key
            if seen_name == name_norm and seen_type == type_norm:
                is_duplicate = True
                merge_relevant_sequences(seen[seen_key], auth)
                if isinstance(seen[seen_key], AuthProcessingInfo) and isinstance(auth, AuthProcessingInfo):
                    _merge_quirks(seen[seen_key], auth)  # type: ignore # - we check that with type
                break

            if seen_name in name_norm and seen_type == type_norm:
                delete_from_seen = seen_key
                merge_relevant_sequences(auth, seen[seen_key])
                if isinstance(auth, AuthProcessingInfo) and isinstance(seen[seen_key], AuthProcessingInfo):
                    _merge_quirks(auth, seen[seen_key])  # type: ignore # - we check that with type
                break

            if name_norm in seen_name and type_norm == seen_type:
                is_duplicate = True
                merge_relevant_sequences(seen[seen_key], auth)
                if isinstance(seen[seen_key], AuthProcessingInfo) and isinstance(auth, AuthProcessingInfo):
                    _merge_quirks(seen[seen_key], auth)  # type: ignore # - we check that with type
                break

        if delete_from_seen is not None:
            del seen[delete_from_seen]

        if not is_duplicate:
            seen[key] = auth

    return list(seen.values())


async def _to_processing_info(auth: DiscoveryAuth | AuthProcessingInfo) -> AuthProcessingInfo:
    """Normalize a deduped entry to AuthProcessingInfo, resolving raw sequence text when needed."""
    if isinstance(auth, AuthProcessingInfo):
        relevant_seq = auth.relevant_sequences
    else:
        relevant_seq = []
        for seq in auth.relevant_sequences:
            sequence_text = getattr(seq, "text", None)
            if not isinstance(sequence_text, str):
                sequence_text = await extract_sequence(
                    seq.chunk_id,
                    seq.start_sequence,
                    seq.end_sequence,
                    logger_prefix="[Digester:Auth:Deduplication] ",
                )
            relevant_seq.append(
                DocProcessingSequenceItem(
                    chunk_id=seq.chunk_id,
                    start_sequence=seq.start_sequence,
                    end_sequence=seq.end_sequence,
                    text=sequence_text,
                )
            )

    return AuthProcessingInfo(
        name=auth.name,
        type=auth.type,
        quirks=getattr(auth, "quirks", ""),
        relevant_sequences=relevant_seq,
    )


def _apply_dedup_plan(auth_list: List[AuthProcessingInfo], result: AuthDedupResponse) -> List[AuthProcessingInfo]:
    """Apply the LLM merge plan: fold each (keep, delete) pair together, then drop deletions."""
    to_dedup = _order_dedup_pairs(result.duplicates or [])
    logger.debug("[Digester:Auth] Pairs to deduplicate (keep, delete): %s", to_dedup)
    logger.debug("[Digester:Auth] Current auth list before deduplication: %s", auth_list)

    mark_for_deletion: List[Tuple[str, str]] = []
    for (keep_name, keep_type), (delete_name, delete_type) in to_dedup:
        keep_key = auth_match_key(keep_name, keep_type)
        delete_key = auth_match_key(delete_name, delete_type)
        old_auth: AuthProcessingInfo | None = None
        new_auth: AuthProcessingInfo | None = None
        for auth in auth_list:
            auth_key = auth_match_key(auth.name, auth.type)
            if auth_key == delete_key:
                old_auth = auth
            elif auth_key == keep_key:
                new_auth = auth

        if old_auth and new_auth:
            if old_auth in auth_list:
                mark_for_deletion.append(auth_match_key(old_auth.name, old_auth.type))
            if new_auth not in auth_list:
                auth_list.append(new_auth)
            merge_relevant_sequences(new_auth, old_auth)
            _merge_quirks(new_auth, old_auth)
        else:
            logger.warning(
                "[Digester:Auth] Could not find auth to deduplicate. Keep: (%s, %s), Delete: (%s, %s)",
                keep_name,
                keep_type,
                delete_name,
                delete_type,
            )

    for target_key in mark_for_deletion:
        for auth in auth_list:
            if auth_match_key(auth.name, auth.type) == target_key:
                auth_list.remove(auth)
                break

    for delete_name, delete_type in result.to_be_deleted or []:
        delete_key = auth_match_key(delete_name, delete_type)
        for potential_auth in auth_list:
            if auth_match_key(potential_auth.name, potential_auth.type) == delete_key:
                auth_list.remove(potential_auth)
                break

    return auth_list


async def deduplicate_auth(
    auth_info: List[DiscoveryAuth] | List[AuthProcessingInfo],
    job_id: UUID,
) -> List[AuthProcessingInfo]:
    """
    Deduplicate auth info.
    First pass is heurestic deduplication based on name/type similarity and merging relevant sequences for exact duplicates.
    Second pass is LLM-based deduplication.

    Args:
        auth_info: List of DiscoveryAuth instances from all documents
        job_id: Job ID for progress tracking

    Returns:
        List of unique AuthProcessingInfo instances
    """
    await update_job_progress(job_id, stage=JobStage.deduplication, message="Deduplicating auth items")
    logger.info("[Digester:Auth] Starting deduplication and sorting. Total count: %d", len(auth_info))

    dedup_list = _heuristic_dedup(auth_info)
    logger.info("[Digester:Auth] Heurestic deduplication complete. Unique count: %d", len(dedup_list))

    auth_list: List[AuthProcessingInfo] = [await _to_processing_info(auth) for auth in dedup_list]

    chain = build_structured_chain(
        auth_deduplication_system_prompt,
        auth_deduplication_user_prompt,
        AuthDedupResponse,
        user_role="human",
    )

    try:
        result = cast(
            AuthDedupResponse,
            await invoke_llm(
                chain,
                {"auth_list": json.dumps([auth.model_dump() for auth in auth_list])},
                config=RunnableConfig(callbacks=[langfuse_handler], run_name="Digester:DedupAuth"),
            ),
        )
        logger.debug("[Digester:Auth] LLM result in deduplication: %r", (result or ""))

        if not result:
            logger.warning("[Digester:Auth] Deduplication LLM returned empty result; keeping original list.")
            await update_job_progress(
                job_id,
                stage=JobStage.deduplication_finished,
                message="Deduplication returned empty merge plan; keeping current list",
            )
            return auth_list

        auth_list = _apply_dedup_plan(auth_list, result)

        await update_job_progress(job_id, stage=JobStage.deduplication_finished, message="Auth deduplication finished")
        return auth_list

    except Exception as e:
        logger.error("[Digester:Auth] Deduplication LLM call failed. Error: %s", e)
        await update_job_progress(job_id, stage=JobStage.deduplication_failed, message=f"Deduplication failed: {e}")
        await append_job_error(job_id, f"[Digester:Auth] Deduplication LLM call failed: {e}")
        return auth_list


async def processInfoToAuthInfo(info: AuthProcessingInfo) -> AuthInfo:
    return AuthInfo(
        name=info.name,
        type=info.type,
        quirks=info.quirks,
        relevant_sequences=[
            DocSequenceItem(
                chunk_id=seq.chunk_id,
                start_sequence=seq.start_sequence,
                end_sequence=seq.end_sequence,
            )
            for seq in info.relevant_sequences
        ],
    )


async def sort_auth_by_importance(raw_dedup_list: List[AuthProcessingInfo], job_id: UUID) -> AuthResponse[AuthInfo]:
    dedup_list = [await processInfoToAuthInfo(info) for info in raw_dedup_list]
    try:
        logger.info("[Digester:Auth] Sorting via LLM. Items count: %d", len(dedup_list))
        chain = build_structured_chain(
            sort_auth_system_prompt,
            sort_auth_user_prompt,
            AuthResponse[AuthInfo],
            user_role="human",
        )

        items_json = json.dumps([auth.model_dump(exclude={"relevant_sequences"}) for auth in dedup_list])
        sort_result = cast(
            AuthResponse[AuthInfo],
            await invoke_llm(
                chain,
                {"items_json": items_json},
                config=RunnableConfig(callbacks=[langfuse_handler], run_name="Digester:SortAuth"),
            ),
        )

        await update_job_progress(job_id, stage=JobStage.sorting, message="Sorting results by importance")

        if sort_result and sort_result.auth:
            original_map: Dict[Tuple[str, str], AuthInfo] = {auth_match_key(a.name, a.type): a for a in dedup_list}
            used: Set[Tuple[str, str]] = set()
            out: List[AuthInfo] = []
            for auth in sort_result.auth:
                key = auth_match_key(auth.name, auth.type)
                if key in original_map and key not in used:
                    out.append(original_map[key])
                    used.add(key)
            for auth in dedup_list:
                key = auth_match_key(auth.name, auth.type)
                if key not in used:
                    out.append(auth)
            logger.info("[Digester:Auth] Sorting complete. Final count: %d", len(out))
            await update_job_progress(job_id, stage=JobStage.sorting_finished, message="Sorting finished; finalizing")

            return AuthResponse[AuthInfo](auth=out)

        logger.warning("[Digester:Auth]  Sorting LLM returned empty; keeping original order.")
        await update_job_progress(job_id, stage=JobStage.sorting_finished, message="Sorting skipped/empty; finalizing")

        return AuthResponse[AuthInfo](auth=dedup_list)

    except Exception as e:
        logger.error("[Digester:Auth] Sorting pass failed. Error: %s", e)
        await update_job_progress(job_id, stage=JobStage.sorting_failed, message=f"Sorting failed: {e}")
        await append_job_error(job_id, f"[Digester:Auth] Sorting failed: {e}")

        return AuthResponse[AuthInfo](auth=dedup_list)


def _auth_type_counts(auth_items: Any) -> Dict[str, int]:
    items = getattr(auth_items, "auth", auth_items)
    if not items:
        return {}

    type_counts: Counter[str] = Counter()
    for item in items:
        raw_type = item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
        type_value = getattr(raw_type, "value", raw_type)
        type_counts[str(type_value or "unknown")] += 1
    return dict(sorted(type_counts.items()))


async def extract_auth(doc_items: List[dict], job_id: UUID):
    """
    Extract authentication info from multiple documentation items and return merged result with metadata.

    Step 1: Extract raw auth info from each chunk (by chunkId) - processes chunks in parallel
    Step 2: Merge, deduplicate and sort ALL auth info together
    """
    all_auth_info = []
    chunk_id_to_doc_id = build_chunk_id_to_doc_id(doc_items)
    chunk_metadata_map = build_doc_metadata_map(doc_items)

    async def extractor_with_metadata(content: str, job_id: UUID, chunk_id: UUID):
        chunk_metadata = chunk_metadata_map.get(str(chunk_id))
        return await extract_auth_raw(content, job_id, chunk_id, chunk_metadata)

    # Process all chunks in parallel using the generic function
    discovery_results = await run_doc_extractors_concurrently(
        chunk_items=doc_items,
        job_id=job_id,
        extractor=extractor_with_metadata,
    )

    # Collect results from all chunks
    for raw_auth, _has_relevant_data, chunk_id in discovery_results:
        logger.info(
            "[Digester:Auth] Chunk %s: extracted %s auth items",
            chunk_id,
            len(raw_auth),
        )
        all_auth_info.extend(raw_auth)

    all_relevant_chunks = collect_relevant_chunks(discovery_results, chunk_id_to_doc_id, "Digester:Auth")

    logger.info(
        "[Digester:Auth] Auth discovery complete. Total: %s auth items from %s documents. Starting deduplication",
        len(all_auth_info),
        len(doc_items),
    )
    await update_job_progress(job_id, stage=JobStage.discovery_finished, message="Auth discovery finished")
    deduplicated_results = await deduplicate_auth(all_auth_info, job_id)

    logger.info(
        "[Digester:Auth] Deduplication complete. Total: %s unique auth items. Type counts: %s",
        len(deduplicated_results),
        _auth_type_counts(deduplicated_results),
    )

    built_auth_items = await build_auth_items(deduplicated_results, job_id)

    logger.info(
        "[Digester:Auth] Build complete. Total: %s built auth items. Type counts: %s",
        len(built_auth_items),
        _auth_type_counts(built_auth_items),
    )

    final_deduplicated_results = await deduplicate_auth(built_auth_items, job_id)

    logger.info(
        "[Digester:Auth] Deduplication complete. Total: %s unique auth items. Type counts: %s",
        len(final_deduplicated_results),
        _auth_type_counts(final_deduplicated_results),
    )

    sorted_auth_items = await sort_auth_by_importance(final_deduplicated_results, job_id)

    logger.info(
        "[Digester:Auth] Sorting complete. Total: %s sorted auth items. Type counts: %s",
        len(sorted_auth_items.auth or []),
        _auth_type_counts(sorted_auth_items),
    )

    return {
        "result": sorted_auth_items.model_dump(by_alias=True),
        "relevantDocumentations": all_relevant_chunks,
    }
