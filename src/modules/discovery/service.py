# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from langchain_core.runnables.config import RunnableConfig
from langchain_openai import ChatOpenAI

from ...common.enums import JobStage
from ...common.jobs import update_job_progress
from ...common.langfuse import langfuse_handler
from ...common.llm import get_default_llm
from .core.search import search_web
from .prompts.prompts import (
    get_discovery_eval_sys_prompt,
    get_discovery_eval_user_prompt,
    get_discovery_fetch_sys_prompt,
    get_discovery_fetch_user_prompt,
)
from .schema import (
    CandidateLinksInput,
    CandidateLinksOutput,
    PyScrapeFetchReferences,
    PySearchPrompts,
    SearchResult,
)
from .utils.llm_helpers import (
    fetch_parser_response,
    generate_queries_via_llm,
    generate_queries_via_preset,
    make_eval_prompt,
)

logger = logging.getLogger(__name__)

MAX_EVAL_RESULTS = 50


@dataclass(frozen=True)
class DiscoverySearchBatch:
    query: str
    results: List[SearchResult]


def _canonicalize_url(url: str) -> str:
    """Canonicalize URL for deduplication.

    - strips fragments
    - drops common tracking parameters (utm_*, gclid, fbclid, etc.)
    """
    try:
        parts = urlsplit(url.strip())
        keep_params: list[tuple[str, str]] = []
        for k, v in parse_qsl(parts.query, keep_blank_values=True):
            lk = k.lower()
            if lk.startswith("utm_") or lk in {"gclid", "fbclid", "yclid", "mc_cid", "mc_eid"}:
                continue
            keep_params.append((k, v))

        new_query = urlencode(keep_params, doseq=True)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, ""))
    except Exception:
        return url.strip()


def _dedupe_results(batches: Sequence[DiscoverySearchBatch]) -> List[SearchResult]:
    """Merge and deduplicate search results across multiple queries.

    Keeps the first occurrence of a canonical href. Preference is implicitly given to
    earlier queries / earlier results.
    """
    seen: set[str] = set()
    merged: List[SearchResult] = []

    for batch in batches:
        for item in batch.results:
            href = (item.href or "").strip()
            if not href:
                continue
            key = _canonicalize_url(href)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)

    return merged


def _enrich_raw_results(batches: Sequence[DiscoverySearchBatch]) -> List[Dict[str, Any]]:
    """Return a raw list for LLM evaluation that includes the originating query."""
    raw: List[Dict[str, Any]] = []
    for batch in batches:
        for r in batch.results:
            d = r.model_dump()
            d["query"] = batch.query
            raw.append(d)
    return raw


def _limit_for_eval(results: List[SearchResult], *, limit: int = MAX_EVAL_RESULTS) -> List[SearchResult]:
    """Bound the number of items given to evaluator LLM to reduce noise/cost."""
    if len(results) <= limit:
        return results
    return results[:limit]


def query_and_search_candidates(
    *,
    model: ChatOpenAI,
    parser_model: ChatOpenAI,
    user_prompt: str,
    system_prompt: str,
    app: str,
    version: str,
    num_queries: int,
    max_results_per_query: int,
) -> Tuple[List[DiscoverySearchBatch], Any, PySearchPrompts]:
    """Generate multiple queries and run web search for each query.

    Returns:
        (batches, raw_llm_response, parsed_prompts)
    """
    logger.info("Running discovery for application='%s' version='%s' (num_queries=%d)", app, version, num_queries)

    queries, response, parsed = generate_queries_via_llm(
        model=model,
        parser_model=parser_model,
        user_prompt=user_prompt,
        system_prompt=system_prompt,
        num_queries=num_queries,
    )

    # Patch placeholders and drop empties
    patched_queries: List[str] = []
    for q in queries:
        q = (q or "").strip()
        if not q:
            continue
        try:
            q = q.format(app=app, version=version)
        except Exception:
            pass
        if q and q not in patched_queries:
            patched_queries.append(q)

    batches: List[DiscoverySearchBatch] = []
    for q in patched_queries:
        logger.info("Running web search with query: %s", q)
        results = search_web(q, max_results=max_results_per_query)
        batches.append(DiscoverySearchBatch(query=q, results=results))

    return batches, response, parsed


def query_and_search_candidates_preset(
    *,
    app: str,
    version: str,
    num_queries: int,
    max_results_per_query: int,
) -> Tuple[List[DiscoverySearchBatch], str, PySearchPrompts]:
    """Generate multiple preset queries and run web search for each query."""
    logger.info(
        "Running discovery (preset) for application='%s' version='%s' (num_queries=%d)", app, version, num_queries
    )

    queries, preset_used, parsed = generate_queries_via_preset(app, version, num_queries=num_queries)

    batches: List[DiscoverySearchBatch] = []
    for q in queries:
        logger.info("Running web search with preset query: %s", q)
        results = search_web(q, max_results=max_results_per_query)
        batches.append(DiscoverySearchBatch(query=q, results=results))

    return batches, preset_used, parsed


def fetch_candidate_links_simplified(
    *,
    model: ChatOpenAI,
    eval_model: ChatOpenAI,
    parser_model: ChatOpenAI,
    user_prompt: str,
    user_prompt_eval: str,
    system_prompt: str,
    system_prompt_eval: str,
    app: str,
    version: str,
    pydantic_class_template: type[PyScrapeFetchReferences],
    llm_generated_search_query: bool,
    num_queries: int,
    max_results_per_query: int,
) -> Tuple[Any, PySearchPrompts, List[Dict[str, Any]], Any, Optional[PyScrapeFetchReferences]]:
    """End-to-end: generate multiple queries, search, merge/dedupe, evaluate, parse output."""
    if llm_generated_search_query:
        batches, response, parsed_prompts = query_and_search_candidates(
            model=model,
            parser_model=parser_model,
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            app=app,
            version=version,
            num_queries=num_queries,
            max_results_per_query=max_results_per_query,
        )
    else:
        batches, response, parsed_prompts = query_and_search_candidates_preset(
            app=app,
            version=version,
            num_queries=num_queries,
            max_results_per_query=max_results_per_query,
        )

    merged_results = _dedupe_results(batches)
    merged_results_for_eval = _limit_for_eval(merged_results, limit=MAX_EVAL_RESULTS)

    tool_output_raw = [item.model_dump() for item in merged_results_for_eval]
    tool_output_raw_enriched = _enrich_raw_results(batches)

    # Evaluate the merged output
    eval_prompt_template = make_eval_prompt(system_prompt_eval)
    chain = eval_prompt_template | eval_model
    eval_input = user_prompt_eval.format(app, version)

    eval_output: Any = chain.invoke(
        {"tool_output_raw": str(tool_output_raw), "input": eval_input},
        config=RunnableConfig(callbacks=[langfuse_handler]),
    )
    logger.debug("Discovery eval_output raw: %s", str(eval_output))

    # Parse to structured references (bounded attempts)
    max_attempts = 2
    for attempt in range(max_attempts):
        try:
            parsed_eval_output = fetch_parser_response(
                parser_model, cast(str, eval_output.content), pydantic_class_template
            )
            logger.info("Output parsing was successful.")
            return response, parsed_prompts, tool_output_raw_enriched, eval_output, parsed_eval_output
        except Exception as exc:
            logger.info("Parsing attempt %d failed with error: %s", attempt + 1, exc)

    return response, parsed_prompts, tool_output_raw_enriched, eval_output, None


async def _run_discovery_async(app_data: CandidateLinksInput, job_id: UUID) -> CandidateLinksOutput:
    """Run the discovery pipeline (async wrapper for progress updates)."""
    discovery_model = get_default_llm()
    discovery_eval_model = get_default_llm()
    discovery_parser_model = get_default_llm()

    app_version = app_data.application_version or ""
    num_queries = app_data.num_queries
    max_results_per_query = app_data.max_results_per_query

    user_prompt_fetch = get_discovery_fetch_user_prompt(app_data.application_name, app_version, 2)
    user_prompt_eval = get_discovery_eval_user_prompt(app_data.application_name, app_version, 1)

    system_prompt_fetch = get_discovery_fetch_sys_prompt(1)
    system_prompt_eval = get_discovery_eval_sys_prompt()

    await update_job_progress(job_id, stage=JobStage.processing, message="Discovering candidate links")

    def run_sync() -> Tuple[Any, PySearchPrompts, List[Dict[str, Any]], Any, Optional[PyScrapeFetchReferences]]:
        return fetch_candidate_links_simplified(
            model=discovery_model,
            eval_model=discovery_eval_model,
            parser_model=discovery_parser_model,
            user_prompt=user_prompt_fetch,
            user_prompt_eval=user_prompt_eval,
            system_prompt=system_prompt_fetch,
            system_prompt_eval=system_prompt_eval,
            app=app_data.application_name,
            version=app_version,
            pydantic_class_template=PyScrapeFetchReferences,
            llm_generated_search_query=app_data.llm_generated_search_query,
            num_queries=num_queries,
            max_results_per_query=max_results_per_query,
        )

    _model_output, _parsed_prompts, tool_output_raw, _eval_output, parsed = await asyncio.to_thread(run_sync)

    parsed_ref = cast(Optional[PyScrapeFetchReferences], parsed)
    if not parsed_ref:
        candidate_links: List[str] = []
        candidate_links_enriched: List[Dict[str, Any]] = []
    else:
        candidate_links = parsed_ref.urls_to_crawl
        logger.info("Selected urls to crawl next: %s", candidate_links)

        # Build enriched results by matching chosen URLs against the merged raw tool output.
        candidate_links_enriched = []
        try:
            by_href = {
                _canonicalize_url(str(item.get("href", ""))): item for item in tool_output_raw if isinstance(item, dict)
            }
            for url in candidate_links:
                item = by_href.get(_canonicalize_url(url))
                if item:
                    candidate_links_enriched.append(item)
        except Exception as exc:
            logger.info("Failed to extract enriched reference list. Error: %s", exc)
            candidate_links_enriched = []

    logger.info("End of the discovery script.")
    return CandidateLinksOutput(candidate_links=candidate_links, candidate_links_enriched=candidate_links_enriched)


async def fetch_candidate_links(app_data: CandidateLinksInput, job_id: UUID) -> CandidateLinksOutput:
    """Public entrypoint for the discovery module."""
    return await _run_discovery_async(app_data, job_id)
