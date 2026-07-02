# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
import logging
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID

from langchain_core.runnables.config import RunnableConfig

from src.common.jobs import append_job_error
from src.common.langfuse import langfuse_handler
from src.common.llm import build_structured_chain
from src.common.utils.normalize import normalize_endpoint_key
from src.modules.digester.enums import EndpointMethod
from src.modules.digester.extraction.chunk_extraction import extract_single_chunk
from src.modules.digester.extraction.llm_execution import invoke_llm
from src.modules.digester.prompts.connectivity_endpoint_prompts import (
    get_connectivity_endpoint_ranking_system_prompt,
    get_connectivity_endpoint_ranking_user_prompt,
    get_connectivity_endpoint_system_prompt,
    get_connectivity_endpoint_user_prompt,
)
from src.modules.digester.schemas import (
    ConnectivityEndpointInfo,
    ConnectivityEndpointRankingResponse,
    ConnectivityEndpointResponse,
    ExtractedConnectivityEndpointInfo,
    ExtractedConnectivityEndpointResponse,
)

logger = logging.getLogger(__name__)


async def extract_connectivity_endpoint_raw(
    schema: str,
    job_id: UUID,
    chunk_id: Optional[UUID] = None,
    chunk_metadata: Optional[Dict[str, Any]] = None,
    base_api_url: str = "",
) -> Tuple[List[ExtractedConnectivityEndpointInfo], bool]:
    """
    Extract connectivity endpoint candidates from a single documentation chunk.
    Aggregation and final candidate selection are handled in the service layer.
    """

    def parse_fn(result: ExtractedConnectivityEndpointResponse) -> List[ExtractedConnectivityEndpointInfo]:
        return result.endpoints or []

    candidates, has_relevant_data = await extract_single_chunk(
        schema=schema,
        pydantic_model=ExtractedConnectivityEndpointResponse,
        system_prompt=get_connectivity_endpoint_system_prompt,
        user_prompt=get_connectivity_endpoint_user_prompt,
        parse_fn=parse_fn,
        logger_prefix="[Digester:ConnectivityEndpoint] ",
        job_id=job_id,
        chunk_id=chunk_id,
        chunk_metadata=chunk_metadata,
        extra_llm_attrs={"base_api_url": base_api_url},
    )

    logger.info(
        "[Digester:ConnectivityEndpoint] Extraction complete. candidates=%s has_relevant_data=%s",
        len(candidates),
        has_relevant_data,
    )
    return candidates, has_relevant_data


def _deduplicate_connectivity_candidates(
    candidates: List[ExtractedConnectivityEndpointInfo],
    endpoint_chunk_pairs: Dict[Tuple[str, str], Set[Tuple[str, str]]],
) -> List[ConnectivityEndpointInfo]:
    """
    Deduplicate extracted candidates by (path, method) key and attach chunk references.
    Returns a list in insertion order (first occurrence wins for dedup, longer description wins).
    """
    by_key: Dict[Tuple[str, str], ExtractedConnectivityEndpointInfo] = {}
    key_order: List[Tuple[str, str]] = []

    for candidate in candidates:
        key = normalize_endpoint_key(candidate.path, candidate.method)
        if key is None:
            continue

        current = by_key.get(key)
        if current is None:
            by_key[key] = candidate
            key_order.append(key)
            continue

        if len(candidate.description or "") > len(current.description or ""):
            current.description = candidate.description
        if not current.response_content_type and candidate.response_content_type:
            current.response_content_type = candidate.response_content_type
        if not current.request_content_type and candidate.request_content_type:
            current.request_content_type = candidate.request_content_type
        if current.requires_auth is None and candidate.requires_auth is not None:
            current.requires_auth = candidate.requires_auth

    result: List[ConnectivityEndpointInfo] = []
    for key in key_order:
        raw = by_key[key]
        relevant_documentations = [
            {"doc_id": doc_id, "chunk_id": chunk_id}
            for doc_id, chunk_id in sorted(endpoint_chunk_pairs.get(key, set()), key=lambda pair: (pair[0], pair[1]))
        ]
        endpoint = ConnectivityEndpointInfo.model_validate(
            {
                **raw.model_dump(by_alias=True, mode="json"),
                "relevantDocumentations": relevant_documentations,
            }
        )
        result.append(endpoint)

    return result


async def rank_connectivity_candidates(
    candidates: List[ConnectivityEndpointInfo],
    job_id: UUID,
) -> List[ConnectivityEndpointInfo]:
    """
    Call LLM once to rank deduplicated candidates by suitability for connectivity testing.
    Returns candidates in ranked order (most suitable first).
    Falls back to original order on any failure.
    """
    if len(candidates) <= 1:
        return candidates

    candidates_json = json.dumps(
        [
            {
                "method": c.method.value if isinstance(c.method, EndpointMethod) else str(c.method),
                "path": c.path,
                "description": c.description,
                "requiresAuth": c.requires_auth,
            }
            for c in candidates
        ],
        indent=2,
    )

    chain = build_structured_chain(
        get_connectivity_endpoint_ranking_system_prompt,
        get_connectivity_endpoint_ranking_user_prompt,
        ConnectivityEndpointRankingResponse,
        user_role="human",
    )

    try:
        result = await invoke_llm(
            chain,
            {"candidates": candidates_json, "count": len(candidates)},
            config=RunnableConfig(callbacks=[langfuse_handler], run_name="Digester:RankConnectivityEndpoints"),
        )
        if not result or not result.ranked_endpoints:
            logger.warning("[Digester:ConnectivityEndpoint] Ranking LLM returned empty result, using original order")
            return candidates

        # Build a lookup by (method, normalized_path)
        by_key: Dict[Tuple[str, str], ConnectivityEndpointInfo] = {}
        for c in candidates:
            key = normalize_endpoint_key(c.path, c.method)
            if key:
                by_key[key] = c

        ranked: List[ConnectivityEndpointInfo] = []
        seen_keys: Set[Tuple[str, str]] = set()
        for ranked_key in result.ranked_endpoints:
            key = normalize_endpoint_key(ranked_key.path, ranked_key.method)
            if key and key in by_key and key not in seen_keys:
                ranked.append(by_key[key])
                seen_keys.add(key)

        # Append any candidates the LLM omitted (safety net)
        for c in candidates:
            key = normalize_endpoint_key(c.path, c.method)
            if key and key not in seen_keys:
                ranked.append(c)

        logger.info(
            "[Digester:ConnectivityEndpoint] Ranking complete. ranked=%s/%s",
            len(ranked),
            len(candidates),
        )
        return ranked

    except Exception as exc:
        error_msg = f"[Digester:ConnectivityEndpoint] Ranking LLM call failed: {exc}"
        logger.exception(error_msg)
        append_job_error(job_id, error_msg)
        return candidates


async def merge_and_rank_connectivity_endpoint_candidates(
    candidates: List[ExtractedConnectivityEndpointInfo],
    endpoint_chunk_pairs: Dict[Tuple[str, str], Set[Tuple[str, str]]],
    job_id: UUID,
) -> ConnectivityEndpointResponse:
    """
    Deduplicate extracted candidates and rank them by connectivity testing suitability via a single LLM call.
    """
    deduped = _deduplicate_connectivity_candidates(candidates, endpoint_chunk_pairs)
    if not deduped:
        return ConnectivityEndpointResponse(endpoints=[])

    ranked = await rank_connectivity_candidates(deduped, job_id)
    return ConnectivityEndpointResponse(endpoints=ranked)
