# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables.config import RunnableConfig

from ....common.langfuse import langfuse_handler
from ....common.llm import get_default_llm, make_basic_chain
from ...scrape.llms import get_irrelevant_llm_response
from ..prompts.prompts import get_irrelevant_filter_prompts, get_rank_links_prompts
from ..schema import RankedLinks

logger = logging.getLogger(__name__)


def _build_prompt_entries(candidates_enriched: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    for item in candidates_enriched:
        url = str(item.get("href", "")).strip()
        if not url:
            continue
        title = str(item.get("title", "")).strip()
        snippet = str(item.get("body", "")).strip()
        entries.append({"url": url, "title": title, "snippet": snippet})
    return entries


async def filter_candidate_links(
    candidates_enriched: List[Dict[str, Any]],
    app: str,
    app_version: str,
    max_llm_calls: int = 3,
) -> tuple[List[str], List[str]]:
    """
    Filter candidate links to remove irrelevant ones using LLM analysis.

    Args:
        candidates_enriched: List of candidate links with metadata (href/title/body)
        app: Application name
        app_version: Application version
        max_llm_calls: Maximum number of LLM filtering iterations

    Returns:
        Tuple of (relevant_links, irrelevant_links)
    """
    logger.info("[Discovery:Filter] Starting to filter %s candidate links", len(candidates_enriched))

    entries = _build_prompt_entries(candidates_enriched)
    if not entries:
        return [], []

    forbidden_url_parts: list[str] = [
        "logout",
        "login",
        "signup",
        "register",
        "subscribe",
        "pricing",
        "terms",
        "privacy",
        "contact",
        "about",
        "blog",
        "news",
        "forum",
        "release-notes",
        "changelog",
    ]

    # Filter out obviously irrelevant links by URL patterns
    basic_irrelevant = []
    filtered_entries: List[Dict[str, str]] = []

    for entry in entries:
        link_lower = entry["url"].lower()
        is_irrelevant = any(part in link_lower for part in forbidden_url_parts)

        if is_irrelevant:
            basic_irrelevant.append(entry["url"])
        else:
            filtered_entries.append(entry)

    filtered_links = [entry["url"] for entry in filtered_entries]

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
            relevant_entries = [entry for entry in filtered_entries if entry["url"] in relevant_links]
            irrelevant_prompts = get_irrelevant_filter_prompts(relevant_entries, app, app_version)
            irrelevant_llm_response = await get_irrelevant_llm_response(irrelevant_prompts)

            if irrelevant_llm_response and irrelevant_llm_response.links:
                logger.info("[Discovery:Filter] LLM identified %s irrelevant links", len(irrelevant_llm_response.links))
                llm_irrelevant.extend(irrelevant_llm_response.links)

                # Remove identified irrelevant links from relevant list
                irrelevant_set = set(irrelevant_llm_response.links)
                relevant_links = [link for link in relevant_links if link not in irrelevant_set]
                filtered_entries = [entry for entry in filtered_entries if entry["url"] not in irrelevant_set]

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


async def rank_candidate_links(
    candidates_enriched: List[Dict[str, Any]],
    app: str,
    app_version: str,
    max_links: int,
) -> List[str]:
    """
    Rank candidate links by relevance using LLM and return ordered URLs.

    Args:
        candidates_enriched: List of candidate links with metadata (href/title/body)
        app: Application name
        app_version: Application version
        max_links: Maximum number of links to return (<=0 means no limit)

    Returns:
        Ordered list of URLs (most relevant first).
    """
    entries = _build_prompt_entries(candidates_enriched)
    if not entries:
        return []

    logger.info(
        "[Discovery:Rank] Starting LLM ranking for %s links (top=%s)",
        len(entries),
        max_links if max_links > 0 else "all",
    )

    developer_msg, user_msg = get_rank_links_prompts(entries, app, app_version)

    llm = get_default_llm(temperature=0.7)
    developer_message = SystemMessage(content=developer_msg)
    developer_message.additional_kwargs = {"__openai_role__": "developer"}
    user_message = HumanMessage(content=user_msg)
    user_message.additional_kwargs = {"__openai_role__": "user"}

    chat_prompts = ChatPromptTemplate.from_messages([developer_message, user_message])
    chain = make_basic_chain(
        prompt=chat_prompts,
        llm=llm,
        parser=PydanticOutputParser(pydantic_object=RankedLinks),
    )

    try:
        ranked = await chain.ainvoke({}, config=RunnableConfig(callbacks=[langfuse_handler]))
    except Exception as exc:
        logger.error("[Discovery:Rank] LLM ranking call failed: %s", exc)
        ranked = None

    original_urls = [entry["url"] for entry in entries]
    allowed = set(original_urls)
    ordered: List[str] = []
    seen: set[str] = set()

    if ranked and ranked.links:
        for url in ranked.links:
            url = str(url).strip()
            if url in allowed and url not in seen:
                ordered.append(url)
                seen.add(url)

    if not ordered:
        ordered = original_urls
    else:
        for url in original_urls:
            if url not in seen:
                ordered.append(url)
                seen.add(url)

    if max_links > 0:
        ranked = ordered[:max_links]
    else:
        ranked = ordered

    logger.info("[Discovery:Rank] Ranking complete: returning %s links", len(ranked))
    return ranked
