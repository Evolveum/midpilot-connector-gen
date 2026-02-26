# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.


import asyncio
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from ...common.database.config import async_session_maker
from ...common.database.repositories.job_repository import JobRepository
from ...common.enums import JobStage
from ...common.jobs import update_job_progress
from ...config import config
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
    resolve_ranking_settings,
    select_links_by_query,
)
from .utils.filter_helpers import filter_candidate_links, rank_candidate_links

logger = logging.getLogger(__name__)


async def discover_candidate_links(
    app_data: CandidateLinksInput,
    session_id: Optional[UUID] = None,
    *,
    job_id: UUID,
) -> CandidateLinksOutput:
    """Job worker: discover candidate links; optionally filter irrelevant links.

    Router should schedule THIS function (single entrypoint).
    Filtering is controlled by optional request fields:
      - enable_link_filtering: bool (default True)
      - max_filter_llm_calls: int (default 3)
    Ranking is controlled by:
      - enable_link_ranking: bool (default True)

    If filtering is disabled, results are returned as discovered (deduped).
    """
    if app_data.use_previous_session_data and session_id:
        logger.info(
            "[Discovery] Job %s (session %s): use_previous_session_data is True, checking for previous discovery output",
            str(job_id),
            str(session_id),
        )
        async with async_session_maker() as db:
            job_repo = JobRepository(db)
            created_at_limits = datetime.now() - config.search.discovery_input_check_interval
            latest_job = await job_repo.get_discovery_job_by_input(
                app_data.model_dump(by_alias=True), created_at_limits
            )
            if latest_job and latest_job.result:
                try:
                    reused_output = CandidateLinksOutput.model_validate(latest_job.result)
                    await update_job_progress(
                        job_id,
                        stage=JobStage.processing,
                        message=f"Reused discovery output from job {latest_job.job_id}",
                    )
                    logger.info(
                        "[Discovery] Job %s: Reusing discovery output from job %s created at %s",
                        str(job_id),
                        str(latest_job.job_id),
                        datetime.isoformat(latest_job.created_at),
                    )
                    return reused_output
                except Exception as exc:
                    logger.warning(
                        "[Discovery] Job %s: Previous job %s has invalid result payload (%s), running fresh discovery",
                        str(job_id),
                        str(latest_job.job_id),
                        str(exc),
                    )
            else:
                logger.info(
                    "[Discovery] Job %s: No previous finished discovery job found with same input since %s",
                    str(job_id),
                    datetime.isoformat(created_at_limits),
                )

    discovery_model, discovery_parser_model = resolve_discovery_models(app_data)
    enable_filtering, max_filter_llm_calls = resolve_filtering_settings(app_data)
    enable_ranking = resolve_ranking_settings(app_data)

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

    irrelevant_links: list[str] = []
    if enable_filtering and candidate_links:
        relevant_links, irrelevant_links = await filter_candidate_links(
            candidates_enriched=candidates_enriched,
            app=app_data.application_name,
            app_version=app_version,
            max_llm_calls=max_filter_llm_calls,
        )

        candidates_enriched = filter_enriched_by_links(candidates_enriched, relevant_links)
        candidate_links = relevant_links

    if candidate_links:
        if enable_filtering and enable_ranking:
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
        else:
            candidate_links = select_links_by_query(candidates_enriched, max_links=app_data.max_candidate_links)
            if app_data.max_candidate_links > 0:
                candidates_enriched = order_enriched_by_links(candidates_enriched, candidate_links)
            logger.info(
                "Selected urls to crawl next (top %s, per query): %s",
                app_data.max_candidate_links,
                candidate_links,
            )
    else:
        logger.info("Selected urls to crawl next: %s", candidate_links)

    if irrelevant_links:
        logger.info("Filtered out irrelevant urls: %s", irrelevant_links)

    logger.debug("Discovery raw output: %s", raw_output)
    logger.info("End of the discovery script.")

    return CandidateLinksOutput(
        candidate_links=candidate_links,
        candidate_links_enriched=candidates_enriched,
    )


async def fetch_candidate_links(app_data: CandidateLinksInput, job_id: UUID) -> CandidateLinksOutput:
    """Backward-compatible alias."""
    return await discover_candidate_links(app_data, None, job_id=job_id)
