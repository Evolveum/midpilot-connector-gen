# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from langchain_openai import ChatOpenAI

from ....common.llm import get_default_llm
from ..core.search import search_web
from ..schema import CandidateLinksInput, DiscoverySearchBatch, PySearchPrompts
from .llm_helpers import generate_queries_via_llm, generate_queries_via_preset

logger = logging.getLogger(__name__)

_TRACKING_PARAMS = {"gclid", "fbclid", "yclid", "mc_cid", "mc_eid"}


def canonicalize_url(url: str) -> str:
    """Canonicalize URL for deduplication."""
    try:
        parts = urlsplit(url.strip())
        keep_params: list[tuple[str, str]] = []
        for k, v in parse_qsl(parts.query, keep_blank_values=True):
            lk = k.lower()
            if lk.startswith("utm_") or lk in _TRACKING_PARAMS:
                continue
            keep_params.append((k, v))

        new_query = urlencode(keep_params, doseq=True)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, ""))
    except Exception:
        return url.strip()


def dedupe_enriched_results(batches: Sequence[DiscoverySearchBatch]) -> List[Dict[str, Any]]:
    """Deduplicate results across batches but keep 'query' attribution."""
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []

    for batch in batches:
        for r in batch.results:
            href = (r.href or "").strip()
            if not href:
                continue

            key = canonicalize_url(href)
            if key in seen:
                continue
            seen.add(key)

            d = r.model_dump()
            d["query"] = batch.query
            out.append(d)

    return out


def extract_links(enriched: Sequence[Dict[str, Any]]) -> List[str]:
    """Extract href values, preserving order, skipping empties."""
    links: List[str] = []
    for item in enriched:
        href = str(item.get("href", "")).strip()
        if href:
            links.append(href)
    return links


def filter_enriched_by_links(
    enriched: Sequence[Dict[str, Any]],
    allowed_links: Sequence[str],
) -> List[Dict[str, Any]]:
    allowed = {str(x).strip() for x in allowed_links if str(x).strip()}
    return [item for item in enriched if str(item.get("href", "")).strip() in allowed]


def query_and_search_candidates_llm(
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
    """Generate multiple queries via LLM and run web search for each query."""
    logger.info(
        "Running discovery for application='%s' version='%s' (num_queries=%d)",
        app,
        version,
        num_queries,
    )

    queries, response, parsed = generate_queries_via_llm(
        model=model,
        parser_model=parser_model,
        user_prompt=user_prompt,
        system_prompt=system_prompt,
        num_queries=num_queries,
    )

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


def query_and_search_candidates_template(
    *,
    app: str,
    version: str,
    num_queries: int,
    max_results_per_query: int,
) -> Tuple[List[DiscoverySearchBatch], str, PySearchPrompts]:
    """Generate multiple template queries and run web search for each query."""
    logger.info(
        "Running discovery (templates) for application='%s' version='%s' (num_queries=%d)",
        app,
        version,
        num_queries,
    )

    queries, preset_used, parsed = generate_queries_via_preset(app, version, num_queries=num_queries)

    batches: List[DiscoverySearchBatch] = []
    for q in queries:
        logger.info("Running web search with template query: %s", q)
        results = search_web(q, max_results=max_results_per_query)
        batches.append(DiscoverySearchBatch(query=q, results=results))

    return batches, preset_used, parsed


def fetch_candidate_links_simplified(
    *,
    model: Optional[ChatOpenAI],
    parser_model: Optional[ChatOpenAI],
    user_prompt: str,
    system_prompt: str,
    app: str,
    version: str,
    llm_generated_search_query: bool,
    num_queries: int,
    max_results_per_query: int,
) -> Tuple[Any, PySearchPrompts, List[Dict[str, Any]]]:
    """Generate queries (LLM or template), search, and return deduped enriched results."""
    if llm_generated_search_query:
        if model is None or parser_model is None:
            raise ValueError("model and parser_model must be provided when llm_generated_search_query=True")
        batches, response, parsed_prompts = query_and_search_candidates_llm(
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
        batches, response, parsed_prompts = query_and_search_candidates_template(
            app=app,
            version=version,
            num_queries=num_queries,
            max_results_per_query=max_results_per_query,
        )

    deduped_enriched = dedupe_enriched_results(batches)
    return response, parsed_prompts, deduped_enriched


def resolve_filtering_settings(app_data: CandidateLinksInput) -> tuple[bool, int]:
    """Resolve filtering-related settings from input (with safe defaults)."""
    enable = bool(getattr(app_data, "enable_link_filtering", True))
    max_calls = int(getattr(app_data, "max_filter_llm_calls", 3) or 3)
    if max_calls < 1:
        max_calls = 1
    return enable, max_calls


def resolve_discovery_models(app_data: CandidateLinksInput) -> tuple[Optional[ChatOpenAI], Optional[ChatOpenAI]]:
    """Return (discovery_model, discovery_parser_model) depending on settings."""
    if app_data.llm_generated_search_query:
        return get_default_llm(), get_default_llm()
    return None, None
