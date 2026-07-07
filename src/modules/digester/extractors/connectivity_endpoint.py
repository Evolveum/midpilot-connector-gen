# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
import logging
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID

from langchain_core.runnables.config import RunnableConfig

from src.common.chunk_filter.filter import filter_documentation_items
from src.common.enums import JobStage
from src.common.jobs import append_job_error, update_job_progress
from src.common.langfuse import langfuse_handler
from src.common.llm import build_structured_chain
from src.common.utils.coerce import as_list
from src.common.utils.normalize import normalize_endpoint_key
from src.modules.digester.entities.object_classes import build_endpoint_result
from src.modules.digester.enums import EndpointMethod
from src.modules.digester.extraction.chunk_extraction import extract_single_chunk, run_doc_extractors_concurrently
from src.modules.digester.extraction.llm_execution import invoke_llm
from src.modules.digester.extraction.metadata_helper import build_doc_metadata_map
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
from src.modules.digester.selection import (
    CONNECTIVITY_ENDPOINT_FALLBACK_CRITERIA,
    build_chunk_id_to_doc_id,
    exclude_doc_items_by_chunk_id,
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


def _connectivity_endpoint_result_has_item(extraction_result: Dict[str, Any]) -> bool:
    result_data = extraction_result.get("result", extraction_result)
    return isinstance(result_data, dict) and bool(result_data.get("endpoints"))


async def _extract_connectivity_endpoint_from_doc_items(
    doc_items: List[dict],
    job_id: UUID,
    base_api_url: str,
) -> Dict[str, Any]:
    if not doc_items:
        return build_endpoint_result()

    all_candidates: List[ExtractedConnectivityEndpointInfo] = []
    all_relevant_chunks: List[Dict[str, Any]] = []
    endpoint_chunk_pairs: Dict[Tuple[str, str], Set[Tuple[str, str]]] = {}
    chunk_id_to_doc_id = build_chunk_id_to_doc_id(doc_items)
    chunk_metadata_map = build_doc_metadata_map(doc_items)

    async def extractor_with_metadata(content: str, job_id: UUID, chunk_id: UUID):
        chunk_metadata = chunk_metadata_map.get(str(chunk_id))
        return await extract_connectivity_endpoint_raw(
            content,
            job_id,
            chunk_id,
            chunk_metadata,
            base_api_url=base_api_url,
        )

    results = await run_doc_extractors_concurrently(
        chunk_items=doc_items,
        job_id=job_id,
        extractor=extractor_with_metadata,
        logger_scope="Digester:ConnectivityEndpoint",
    )

    for candidates, has_relevant_data, chunk_id in results:
        chunk_id_str = str(chunk_id)
        doc_id = chunk_id_to_doc_id.get(chunk_id_str)

        candidates_for_chunk = as_list(candidates)
        all_candidates.extend(candidates_for_chunk)

        if not has_relevant_data:
            continue

        if not doc_id:
            logger.warning(
                "[Digester:ConnectivityEndpoint] Missing docId for chunk %s, skipping relevant chunk mapping",
                chunk_id_str,
            )
            continue

        chunk_ref = {"doc_id": doc_id, "chunk_id": chunk_id_str}
        all_relevant_chunks.append(chunk_ref)
        for candidate in candidates_for_chunk:
            key = normalize_endpoint_key(candidate.path, candidate.method)
            if key:
                endpoint_chunk_pairs.setdefault(key, set()).add((doc_id, chunk_id_str))

    await update_job_progress(
        job_id,
        stage=JobStage.deduplication,
        message="Ranking connectivity endpoint candidates",
    )
    response = await merge_and_rank_connectivity_endpoint_candidates(all_candidates, endpoint_chunk_pairs, job_id)

    await update_job_progress(
        job_id,
        stage=JobStage.schema_ready,
        message="Connectivity endpoint extraction complete",
    )
    return {
        "result": response.model_dump(by_alias=True, mode="json"),
        "relevantDocumentations": all_relevant_chunks,
    }


async def extract_connectivity_endpoint(
    doc_items: List[dict],
    session_id: UUID,
    job_id: UUID,
    base_api_url: str = "",
) -> Dict[str, Any]:
    """
    Extract and rank endpoints suitable for testing connectivity between midPoint connector generator and the target app.
    Retries with broader documentation criteria when endpoint-focused chunks produce no candidates.
    """
    result = await _extract_connectivity_endpoint_from_doc_items(doc_items, job_id, base_api_url)
    if _connectivity_endpoint_result_has_item(result):
        return result

    logger.info(
        "[Digester:ConnectivityEndpoint] Primary documentation produced no connectivity endpoint for session %s; "
        "retrying with fallback criteria",
        session_id,
    )
    await update_job_progress(
        job_id,
        stage="chunking",
        message="No connectivity endpoint found in primary chunks; retrying with broader filter",
    )

    fallback_doc_items = await filter_documentation_items(CONNECTIVITY_ENDPOINT_FALLBACK_CRITERIA, session_id)
    if not fallback_doc_items:
        logger.info(
            "[Digester:ConnectivityEndpoint] Fallback criteria matched no documentation for session %s",
            session_id,
        )
        return result

    primary_chunk_ids = {str(item.get("chunkId") or "").strip() for item in doc_items if item.get("chunkId")}
    fallback_doc_items = exclude_doc_items_by_chunk_id(fallback_doc_items, primary_chunk_ids)
    if not fallback_doc_items:
        logger.info(
            "[Digester:ConnectivityEndpoint] Fallback criteria produced no new chunks for session %s",
            session_id,
        )
        return result

    fallback_result = await _extract_connectivity_endpoint_from_doc_items(fallback_doc_items, job_id, base_api_url)
    if _connectivity_endpoint_result_has_item(fallback_result):
        return fallback_result

    return result
