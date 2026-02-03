# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import List

from ....config import config
from ...scrape.llms import get_irrelevant_llm_response
from ...scrape.prompts import get_irrelevant_filter_prompts

logger = logging.getLogger(__name__)


async def filter_candidate_links(
    links: List[str],
    app: str,
    app_version: str,
    max_llm_calls: int = 3,
) -> tuple[List[str], List[str]]:
    """
    Filter candidate links to remove irrelevant ones using LLM analysis.

    Args:
        links: List of candidate links to filter
        app: Application name
        app_version: Application version
        max_llm_calls: Maximum number of LLM filtering iterations

    Returns:
        Tuple of (relevant_links, irrelevant_links)
    """
    logger.info("[Discovery:Filter] Starting to filter %s candidate links", len(links))

    if not links:
        return [], []

    forbidden_url_parts = config.scrape_and_process.forbidden_url_parts

    # Filter out obviously irrelevant links by URL patterns
    filtered_links = []
    basic_irrelevant = []

    for link in links:
        link_lower = link.lower()
        is_irrelevant = any(part in link_lower for part in forbidden_url_parts)

        if is_irrelevant:
            basic_irrelevant.append(link)
        else:
            filtered_links.append(link)

    logger.info(
        "[Discovery:Filter] After basic URL filtering: %s links remain, %s removed",
        len(filtered_links),
        len(basic_irrelevant),
    )

    # LLM-based filtering for remaining links
    llm_irrelevant = []
    relevant_links = filtered_links.copy()

    for call_num in range(max_llm_calls):
        if not relevant_links:
            break

        logger.info(
            "[Discovery:Filter] Starting LLM filtering call %s/%s with %s links via scrape.llms",
            call_num + 1,
            max_llm_calls,
            len(relevant_links),
        )

        try:
            irrelevant_prompts = get_irrelevant_filter_prompts(relevant_links, app, app_version)
            irrelevant_llm_response = await get_irrelevant_llm_response(irrelevant_prompts)

            if irrelevant_llm_response and irrelevant_llm_response.links:
                logger.info("[Discovery:Filter] LLM identified %s irrelevant links", len(irrelevant_llm_response.links))
                llm_irrelevant.extend(irrelevant_llm_response.links)

                # Remove identified irrelevant links from relevant list
                relevant_links = [link for link in relevant_links if link not in irrelevant_llm_response.links]

            else:
                logger.warning("[Discovery:Filter] LLM returned no irrelevant links or failed")

        except Exception as e:
            logger.error("[Discovery:Filter] LLM filtering call %s failed: %s", call_num + 1, e)

    all_irrelevant = basic_irrelevant + llm_irrelevant
    logger.info(
        "[Discovery:Filter] Filtering complete: %s relevant links, %s total irrelevant links",
        len(relevant_links),
        len(all_irrelevant),
    )

    return relevant_links, all_irrelevant
