# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
from typing import List

from crawl4ai.utils import get_base_domain  # type: ignore
from pydantic import HttpUrl

from src.common.documentation import SavedDocumentation
from src.common.schema import validate_pydantic_object
from src.config import config
from src.modules.scrape.core.links import is_forbidden_url
from src.modules.scrape.core.llms import get_irrelevant_llm_response
from src.modules.scrape.prompts.prompts import get_irrelevant_filter_prompts
from src.modules.scrape.schema import IrrelevantLinks

logger = logging.getLogger(__name__)


async def process_irrelevant_link_batch(
    irrelevant_links_part: list[str], app: str, app_version: str
) -> IrrelevantLinks | None:
    irrelevant_prompts = get_irrelevant_filter_prompts(irrelevant_links_part, app, app_version)
    return await get_irrelevant_llm_response(irrelevant_prompts)


async def filter_out_irrelevant_links(
    links: list[str],
    saved_documentations: dict[str, SavedDocumentation],
    trusted_domains: list[str],
    app: str,
    app_version: str,
    past_irrelevant_links: list[str],
    forbidden_url_parts: list[str],
    call_llm: bool = True,
    llm_calls: int = 5,
    current_scraped_urls: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """
    Filter out irrelevant links using multiple methods.
    1) Remove already evaluated links.
    2) Keep only links from trusted domains.
    3) Remove links containing forbidden URL parts.
    4) Use LLM to identify irrelevant links.
    inputs:
        links: list - list of links to filter
        saved_documentations: dict - dictionary of already saved documentations
        trusted_domains: list - list of trusted domains
        app: str - application name
        app_version: str - application version
        past_irrelevant_links: list - list of previously identified irrelevant links
        forbidden_url_parts: list - list of URL parts to filter out
        llm_calls: int - number of LLM calls to make
        current_scraped_urls: list - list of links already queued for the next iteration (to avoid duplicates)
    outputs:
        list - list of irrelevant links from this run
        list - filtered list of relevant links
    """
    links_set = set(links)
    logger.info("[Scrape:Filter] Filtering %s unique candidate links", len(links_set))

    current_links = [
        link
        for link in list(links_set)
        if link not in saved_documentations
        and link + "/" not in saved_documentations
        and (
            current_scraped_urls is None
            or (link not in current_scraped_urls and link + "/" not in current_scraped_urls)
        )
    ]
    logger.debug(
        "[Scrape:Filter] Remove already-processed links: %s -> %s (removed %s)",
        len(links_set),
        len(current_links),
        len(links_set) - len(current_links),
    )

    current_links_past_filtered = list(set(current_links) - set(past_irrelevant_links))
    logger.debug(
        "[Scrape:Filter] Remove links previously marked irrelevant: %s -> %s (removed %s)",
        len(current_links),
        len(current_links_past_filtered),
        len(current_links) - len(current_links_past_filtered),
    )

    current_links_trusted = [
        link
        for link in current_links_past_filtered
        if get_base_domain(link) in trusted_domains or "netsuite" in get_base_domain(link)
    ]
    logger.debug(
        "[Scrape:Filter] Keep trusted domains only: %s -> %s (removed %s)",
        len(current_links_past_filtered),
        len(current_links_trusted),
        len(current_links_past_filtered) - len(current_links_trusted),
    )

    current_links_trusted_valid = [link for link in current_links_trusted if validate_pydantic_object(link, HttpUrl)]
    logger.debug(
        "[Scrape:Filter] Validate URL format: %s -> %s (removed %s)",
        len(current_links_trusted),
        len(current_links_trusted_valid),
        len(current_links_trusted) - len(current_links_trusted_valid),
    )
    past_irrelevant_links.extend(list(set(current_links_past_filtered) - set(current_links_trusted_valid)))

    links_to_remove = [link for link in current_links_trusted_valid if is_forbidden_url(link, forbidden_url_parts)]
    past_irrelevant_links.extend(links_to_remove)
    current_links_not_forbidden = [link for link in current_links_trusted_valid if link not in links_to_remove]
    logger.debug(
        "[Scrape:Filter] Remove forbidden URL parts: %s -> %s (removed %s)",
        len(current_links_trusted_valid),
        len(current_links_not_forbidden),
        len(current_links_trusted_valid) - len(current_links_not_forbidden),
    )

    if not call_llm:
        new_irrelevant_links = list(links_set - set(current_links_not_forbidden))
        logger.debug("[Scrape:Filter] LLM filtering disabled")
        logger.info(
            "[Scrape:Filter] Filtering complete: %s relevant links, %s irrelevant in this batch, %s total tracked irrelevant",
            len(current_links_not_forbidden),
            len(new_irrelevant_links),
            len(set(past_irrelevant_links)),
        )
        return new_irrelevant_links, current_links_not_forbidden

    curr_run = 0
    while curr_run < llm_calls and len(current_links_not_forbidden) > 0:
        logger.debug(
            "[Scrape:Filter] Starting LLM filtering call %s/%s for %s links",
            curr_run + 1,
            llm_calls,
            len(current_links_not_forbidden),
        )

        link_parts: List[List[str]] = []

        step = len(current_links_not_forbidden) / config.scrape_and_process.irrelevant_links_parts
        number_of_steps = min(
            config.scrape_and_process.irrelevant_links_parts_min_length,
            max(1, int(len(current_links_not_forbidden) / step)),
        )
        for i in range(number_of_steps):
            part_links = current_links_not_forbidden[
                int(i * step) : min(int((i + 1) * step), len(current_links_not_forbidden))
            ]
            link_parts.append(part_links)

        irrelevant_llm_responses = await asyncio.gather(
            *[process_irrelevant_link_batch(part, app, app_version) for part in link_parts]
        )

        if any(resp is None for resp in irrelevant_llm_responses):
            logger.warning("[Scrape:Filter] LLM filtering call %s/%s failed", curr_run + 1, llm_calls)
        else:
            irrelevant_llm_links = []
            for resp in irrelevant_llm_responses:
                if resp is not None:
                    irrelevant_llm_links.extend(resp.links)
            logger.debug("[Scrape:Filter] LLM identified %s raw irrelevant links", len(irrelevant_llm_links))
            irrelevant_llm_links = list(set(irrelevant_llm_links) & set(current_links_not_forbidden))
            logger.debug(
                "[Scrape:Filter] LLM identified %s relevant candidates as irrelevant", len(irrelevant_llm_links)
            )
            logger.debug("[Scrape:Filter] LLM irrelevant links: %s", irrelevant_llm_links)

            past_irrelevant_links.extend(irrelevant_llm_links)

            current_links_not_forbidden = list(set(current_links_not_forbidden) - set(past_irrelevant_links))

        curr_run += 1

    new_irrelevant_links = list(links_set - set(current_links_not_forbidden))
    logger.info(
        "[Scrape:Filter] Filtering complete: %s relevant links, %s irrelevant in this batch, %s total tracked irrelevant",
        len(current_links_not_forbidden),
        len(new_irrelevant_links),
        len(set(past_irrelevant_links)),
    )

    return new_irrelevant_links, current_links_not_forbidden
