# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.


import asyncio
import logging
from uuid import UUID

from ...common.enums import JobStage
from ...common.jobs import update_job_progress
from .prompts.prompts import (
    get_discovery_fetch_sys_prompt,
    get_discovery_fetch_user_prompt,
)
from .schema import (
    CandidateLinksInput,
    CandidateLinksOutput,
)
from .utils.discovery_helpers import (
    extract_links,
    fetch_candidate_links_simplified,
    filter_enriched_by_links,
    order_enriched_by_links,
    resolve_discovery_models,
    resolve_filtering_settings,
)
from .utils.filter_helpers import filter_candidate_links, rank_candidate_links

logger = logging.getLogger(__name__)


async def discover_candidate_links(app_data: CandidateLinksInput, job_id: UUID) -> CandidateLinksOutput:
    """Job worker: discover candidate links; optionally filter irrelevant links.

    Router should schedule THIS function (single entrypoint).
    Filtering is controlled by optional request fields:
      - enable_link_filtering: bool (default False)
      - max_filter_llm_calls: int (default 3)

    If filtering is disabled, results are returned as discovered (deduped).
    """
    discovery_model, discovery_parser_model = resolve_discovery_models(app_data)
    enable_filtering, max_filter_llm_calls = resolve_filtering_settings(app_data)

    app_version = app_data.application_version or ""
    user_prompt_fetch = get_discovery_fetch_user_prompt(app_data.application_name, app_version)
    system_prompt_fetch = get_discovery_fetch_sys_prompt()

    filter_msg = "with filtering" if enable_filtering else "without filtering"
    await update_job_progress(
        job_id,
        stage=JobStage.processing,
        message=f"Discovering candidate links {filter_msg}",
    )

    raw_output, parsed_prompts, candidates_enriched = await asyncio.to_thread(
        fetch_candidate_links_simplified,
        model=discovery_model,
        parser_model=discovery_parser_model,
        user_prompt=user_prompt_fetch,
        system_prompt=system_prompt_fetch,
        app=app_data.application_name,
        version=app_version,
        llm_generated_search_query=app_data.llm_generated_search_query,
        num_queries=app_data.num_queries,
        max_results_per_query=app_data.max_results_per_query,
    )
    candidate_links = extract_links(candidates_enriched)

    if enable_filtering and candidate_links:
        relevant_links, irrelevant_links = await filter_candidate_links(
            candidates_enriched=candidates_enriched,
            app=app_data.application_name,
            app_version=app_version,
            max_llm_calls=max_filter_llm_calls,
        )

        candidates_enriched = filter_enriched_by_links(candidates_enriched, relevant_links)
        candidate_links = relevant_links

        ranked_links = await rank_candidate_links(
            candidates_enriched=candidates_enriched,
            app=app_data.application_name,
            app_version=app_version,
            max_links=app_data.max_candidate_links,
        )
        if ranked_links:
            candidate_links = ranked_links
            candidates_enriched = order_enriched_by_links(candidates_enriched, candidate_links)
        if app_data.max_candidate_links > 0:
            candidate_links = candidate_links[: app_data.max_candidate_links]
            candidates_enriched = order_enriched_by_links(candidates_enriched, candidate_links)

        logger.info("Ranked urls to crawl next (top %s): %s", app_data.max_candidate_links, candidate_links)
        if irrelevant_links:
            logger.info("Filtered out irrelevant urls: %s", irrelevant_links)
    else:
        logger.info("Selected urls to crawl next: %s", candidate_links)

    logger.debug("Discovery raw output: %s", raw_output)
    logger.info("End of the discovery script.")

    return CandidateLinksOutput(
        candidate_links=candidate_links,
        candidate_links_enriched=candidates_enriched,
    )


async def fetch_candidate_links(app_data: CandidateLinksInput, job_id: UUID) -> CandidateLinksOutput:
    """Backward-compatible alias."""
    return await discover_candidate_links(app_data, job_id)
