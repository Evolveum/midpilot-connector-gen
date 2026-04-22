# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
import logging
from typing import Dict, List, Optional, Set, Tuple, cast
from uuid import UUID

from langchain_core.output_parsers import BaseOutputParser, PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables.config import RunnableConfig

from src.common.enums import JobStage
from src.common.jobs import append_job_error, update_job_progress
from src.common.langfuse import langfuse_handler
from src.common.llm import get_default_llm, make_basic_chain
from src.modules.digester.prompts.auth_prompts import (
    auth_build_system_prompt,
    auth_build_user_prompt,
    auth_deduplication_system_prompt,
    auth_deduplication_user_prompt,
    get_auth_discovery_system_prompt,
    get_auth_discovery_user_prompt,
)
from src.modules.digester.prompts.rest.sorting_output_prompts import sort_auth_system_prompt, sort_auth_user_prompt
from src.modules.digester.schema import (
    AuthBuildResponse,
    AuthDedupResponse,
    AuthDiscoveryResponse,
    AuthInfo,
    AuthProcessingInfo,
    AuthResponse,
    AuthType,
    DiscoveryAuth,
    DocProcessingSequenceItem,
    DocSequenceItem,
)
from src.modules.digester.utils.chunk_extraction import extract_single_chunk
from src.modules.digester.utils.sequences import extract_sequence

logger = logging.getLogger(__name__)

def _order_dedup_pairs(
    dedup_pairs: List[Tuple[Tuple[str, str], Tuple[str, str]]],
) -> List[Tuple[Tuple[str, str], Tuple[str, str]]]:
    """Topologically order dedup pairs so transitive merges are applied safely.

    If pair A deletes an entry that pair B keeps, B must run before A.
    """

    def _norm_key(item: Tuple[str, str]) -> Tuple[str, str]:
        return (item[0].strip().lower(), AuthProcessingInfo._normalize_auth_type(item[1].strip().lower()))

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

    def parse_fn(result: AuthDiscoveryResponse) -> List[DiscoveryAuth]:
        return result.auth or []

    extracted, has_relevant_data = await extract_single_chunk(
        schema=schema,
        pydantic_model=AuthDiscoveryResponse,
        system_prompt=get_auth_discovery_system_prompt,
        user_prompt=get_auth_discovery_system_prompt,
        parse_fn=parse_fn,
        logger_prefix="[Digester:Auth] ",
        job_id=job_id,
        chunk_id=chunk_id,
        chunk_metadata=chunk_metadata,
        enabled_sequence_checking=True,
    )

    logger.info(
        "[Digester:Auth] Extraction complete. has_relevant_data=%s",
        has_relevant_data,
    )
    return extracted, has_relevant_data


async def deduplicate_and_sort_auth(
    auth_info: List[AuthInfo],
    job_id: UUID,
) -> AuthResponse:
    """
    Deduplicate and sort auth info from all documents.

    Args:
        auth_info: List of AuthInfo instances from all documents
        job_id: Job ID for progress tracking

    Returns:
        AuthResponse with deduplicated and sorted auth info
    """
    logger.info("[Digester:Auth] Starting deduplication and sorting. Total count: %d", len(auth_info))

    # Dedup + merge quirks if it is an exact match
    seen: Dict[Tuple[str, str], AuthInfo] = {}
    for auth in auth_info:
        if not auth or not auth.name:
            continue
        name_norm = (auth.name or "").strip().lower().replace("-", "").replace(" ", "")
        type_norm = (auth.type or "").strip().lower()
        key = (name_norm, type_norm)

        # Check if key is substring of any seen key or if any seen key is substring of key
        # For now, if two keys match perfectly or as substrings, we consider them duplicates
        # only when their types are the same
        # If it is an exact match, we merge quirks, we delete the one with less quirks, otherwise we leave only the key with longer name
        is_duplicate = False
        delete_from_seen: Optional[tuple[str, str]] = None
        for seen_key in seen:
            seen_name, seen_type = seen_key
            # TODO: Ugly code, refactor
            if seen_name == name_norm and seen_type == type_norm:
                if not seen[seen_key].quirks or not auth.quirks:
                    is_duplicate = True
                    # Merge quirks from both
                    seen_q = seen[seen_key].quirks or ""
                    auth_q = auth.quirks or ""
                    if seen_q and auth_q:
                        seen[seen_key].quirks = f"{seen_q}; {auth_q}"
                    elif auth_q:
                        seen[seen_key].quirks = auth_q
                    break
                else:
                    seen_quirks = seen[seen_key].quirks
                    auth_quirks = auth.quirks
                    if (
                        isinstance(seen_quirks, str)
                        and isinstance(auth_quirks, str)
                        and len(seen_quirks) < len(auth_quirks)
                    ):
                        delete_from_seen = seen_key
                        auth.quirks = f"{auth_quirks}; {seen_quirks}"
                        break
                    elif (
                        isinstance(seen_quirks, str)
                        and isinstance(auth_quirks, str)
                        and len(seen_quirks) >= len(auth_quirks)
                    ):
                        is_duplicate = True
                        seen[seen_key].quirks = f"{seen_quirks}; {auth_quirks}"
                        break
                    else:
                        is_duplicate = True
                        break

            if seen_name in name_norm and seen_type == type_norm:
                delete_from_seen = seen_key
                break

            if name_norm in seen_name and type_norm == seen_type:
                is_duplicate = True
                break

        if delete_from_seen is not None:
            del seen[delete_from_seen]

        if not is_duplicate:
            seen[key] = auth

    dedup_list: List[AuthInfo] = list(seen.values())
    logger.info("[Digester:Auth] Deduplication complete. Unique count: %d", len(dedup_list))

    # Progress: chunks processed finished, moving to sorting
    await update_job_progress(
        job_id,
        stage=JobStage.sorting,
        message="Processing chunks finished; now sorting by importance",
    )

    if not dedup_list:
        return AuthResponse(auth=[])

    # Sort by relevance via LLM
    try:
        logger.info("[Digester:Auth] Sorting via LLM. Items count: %d", len(dedup_list))
        parser: PydanticOutputParser[AuthResponse] = PydanticOutputParser(pydantic_object=AuthResponse)
        llm = get_default_llm()
        prompt = ChatPromptTemplate.from_messages(
            [("system", sort_auth_system_prompt + "\n\n{format_instructions}"), ("human", sort_auth_user_prompt)]
        ).partial(format_instructions=parser.get_format_instructions())
        chain = make_basic_chain(prompt, llm, parser)

        items_json = json.dumps([auth.model_dump() for auth in dedup_list])
        sort_result = cast(
            AuthResponse,
            await chain.ainvoke({"items_json": items_json}, config=RunnableConfig(callbacks=[langfuse_handler])),
        )

        await update_job_progress(job_id, stage=JobStage.sorting, message="Sorting results by importance")

        if sort_result and sort_result.auth:
            original_map: Dict[Tuple[str, str], AuthInfo] = {
                (a.name.strip().lower(), a.type.strip().lower()): a for a in dedup_list
            }
            used: Set[Tuple[str, str]] = set()
            out: List[AuthInfo] = []
            for auth in sort_result.auth:
                key = (auth.name.strip().lower(), auth.type.strip().lower())
                if key in original_map and key not in used:
                    out.append(original_map[key])
                    used.add(key)
            for auth in dedup_list:
                key = (auth.name.strip().lower(), auth.type.strip().lower())
                if key not in used:
                    out.append(auth)
            logger.info("[Digester:Auth] Sorting complete. Final count: %d", len(out))
            await update_job_progress(job_id, stage=JobStage.sorting_finished, message="Sorting finished; finalizing")

            return AuthResponse(auth=out)

        logger.warning("[Digester:Auth]  Sorting LLM returned empty; keeping original order.")
        await update_job_progress(job_id, stage="sorting_finished", message="Sorting skipped/empty; finalizing")

        return AuthResponse(auth=dedup_list)

    except Exception as exc:
        error_message = f"[Digester:Auth] Sorting failed: {exc}"
        logger.exception(error_message)
        await update_job_progress(job_id, stage=JobStage.sorting_failed, message=error_message)
        append_job_error(job_id, error_message)

        return AuthResponse(auth=dedup_list)
