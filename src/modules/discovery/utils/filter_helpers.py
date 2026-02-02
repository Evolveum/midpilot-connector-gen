# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import List

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

from ....common.llm import get_default_llm, make_basic_chain
from ...scrape.prompts import get_irrelevant_filter_prompts
from ..schema import IrrelevantLinks

logger = logging.getLogger(__name__)


async def get_irrelevant_llm_response(prompts: tuple[str, str], max_retries: int = 3) -> IrrelevantLinks | None:
    """
    Create and return a ChatOpenAI LLM instance configured for filtering irrelevant links.

    :param prompts: Tuple of (developer_message, user_message) for LLM
    :return: IrrelevantLinks object or None if failed
    """
    logger.debug("[Discovery:Filter] Starting LLM call for irrelevant links filtering")
    developer_msg, user_msg = prompts

    llm = get_default_llm(temperature=1)

    developer_message = SystemMessage(content=developer_msg)
    developer_message.additional_kwargs = {"__openai_role__": "developer"}

    user_message = HumanMessage(content=user_msg)
    user_message.additional_kwargs = {"__openai_role__": "user"}

    req_num = 0
    done = False
    chat_prompts = ChatPromptTemplate.from_messages(
        [
            developer_message,
            user_message,
        ]
    )

    chain = make_basic_chain(
        prompt=chat_prompts,
        llm=llm,
        parser=PydanticOutputParser(pydantic_object=IrrelevantLinks),
    )

    result: IrrelevantLinks | None = None

    while req_num < max_retries and not done:
        try:
            result = await chain.ainvoke({})
            done = True
            logger.debug("[Discovery:Filter] LLM call successful on attempt %s", req_num + 1)
        except Exception as e:
            logger.error("[Discovery:Filter] Error invoking LLM (attempt %s/%s): %s", req_num + 1, max_retries, e)
            req_num += 1

    if not done:
        logger.error("[Discovery:Filter] Failed to get LLM response after %s retries", max_retries)
        raise Exception("Failed to get LLM response after maximum retries")

    return result


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

    # Basic URL filtering first
    forbidden_url_parts = [
        "/get-help/",
        "about/",
        "/contact-us/",
        "/privacy/",
        "/terms/",
        "/blog/",
        "/login",
        "/signup",
        "/register",
        "/pricing",
        "/plans",
        "/news",
        "/forum",
        "/release-notes",
        "/changelog",
    ]

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
            "[Discovery:Filter] Starting LLM filtering call %s/%s with %s links",
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
