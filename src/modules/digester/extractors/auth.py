# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
import logging
from typing import Dict, List, Optional, Set, Tuple, cast
from uuid import UUID

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables.config import RunnableConfig

from ....common.enums import JobStage
from ....common.jobs import append_job_error, update_job_progress
from ....common.langfuse import langfuse_handler
from ....common.llm import get_default_llm, make_basic_chain
from ..prompts.auth_prompts import get_auth_system_prompt, get_auth_user_prompt
from ..prompts.sorting_output_prompts import sort_auth_system_prompt, sort_auth_user_prompt
from ..schema import AuthInfo, AuthResponse
from ..utils.parallel import run_extraction_parallel

logger = logging.getLogger(__name__)


async def extract_auth_raw(
    schema: str, job_id: UUID, doc_id: Optional[UUID] = None, doc_metadata: Optional[Dict] = None
) -> Tuple[List[AuthInfo], bool]:
    """
    Extract raw auth info from a single document with per-chunk parallel LLM calls.
    Does NOT deduplicate or sort - that's done later across all documents.

    Returns:
        - List of raw AuthInfo instances
        - Boolean indicating if relevant data was found
    """

    def parse_fn(result: AuthResponse) -> List[AuthInfo]:
        return result.auth or []

    extracted, has_relevant_data = await run_extraction_parallel(
        schema=schema,
        pydantic_model=AuthResponse,
        system_prompt=get_auth_system_prompt,
        user_prompt=get_auth_user_prompt,
        parse_fn=parse_fn,
        logger_prefix="[Digester:Auth] ",
        job_id=job_id,
        doc_id=doc_id,
        chunk_metadata=doc_metadata,
    )

    logger.info("[Digester:Auth] Auth extracted: %s from document: %s", extracted, doc_id)

    logger.info("[Digester:Auth] Raw extraction complete from document. Count: %d", len(extracted))
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
    update_job_progress(
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

        update_job_progress(job_id, stage=JobStage.sorting, message="Sorting results by importance")

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
            update_job_progress(job_id, stage=JobStage.sorting_finished, message="Sorting finished; finalizing")

            return AuthResponse(auth=out)

        logger.warning("[Digester:Auth]  Sorting LLM returned empty; keeping original order.")
        update_job_progress(job_id, stage="sorting_finished", message="Sorting skipped/empty; finalizing")

        return AuthResponse(auth=dedup_list)

    except Exception as e:
        logger.error("[Digester:Auth] Sorting pass failed. Error: %s", e)
        update_job_progress(job_id, stage=JobStage.sorting_failed, message=f"Sorting failed: {e}")
        append_job_error(job_id, f"[Digester:Auth] Sorting failed: {e}")

        return AuthResponse(auth=dedup_list)
