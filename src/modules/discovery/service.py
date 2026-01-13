"""
Service module to generate a relevant web search query, execute it, and return the relevant links for the scraper.
"""

#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple, cast
from uuid import UUID

import requests
from ddgs import DDGS
from langchain.output_parsers import OutputFixingParser
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables.config import RunnableConfig
from langchain_openai import ChatOpenAI

from ...common.enums import JobStage
from ...common.jobs import update_job_progress
from ...common.langfuse import langfuse_handler
from ...common.llm import get_default_llm_small1, get_default_llm_small2
from ...config import config
from .prompts import (
    get_discovery_eval_sys_prompt,
    get_discovery_eval_user_prompt,
    get_discovery_fetch_sys_prompt,
    get_discovery_fetch_user_prompt,
)
from .schema import CandidateLinksInput, CandidateLinksOutput, PyScrapeFetchReferences, PySearchPrompt

logger = logging.getLogger(__name__)


_DEFAULT_MAX_RESULTS = 10
_HTTP_TIMEOUT_SECS = 15


def _search_with_ddgs(query: str, *, max_results: int = _DEFAULT_MAX_RESULTS) -> List[Dict[str, Any]]:
    """Search via ddgs (DuckDuckGo) helper."""
    logger.info("Web search method: ddgs package")
    try:
        results: List[Dict[str, Any]] = []
        # DDGS supports multiple backends; keep close to original intent.
        with DDGS() as ddgs:
            for item in ddgs.text(
                query,
                max_results=max_results,
                backend=["bing", "brave", "yahoo"],
            ):
                # ddgs returns dicts; normalize keys we actually use downstream
                results.append(
                    {
                        "title": item.get("title") or "",
                        "href": item.get("href") or item.get("url") or "",
                        "body": item.get("body") or item.get("description") or item.get("snippet") or "",
                        "source": "ddgs",
                    }
                )
        logger.info("Web search results count (ddgs): %d", len(results))
        return results
    except Exception:
        logger.exception("DDGS search failed")
        return []


def _search_with_brave(query: str, *, max_results: int = _DEFAULT_MAX_RESULTS) -> List[Dict[str, Any]]:
    """Search via Brave Search API."""
    logger.info("Web search method: Brave API")
    endpoint = config.brave.endpoint
    api_key = config.brave.api_key

    if not endpoint or not api_key:
        logger.warning("Brave API not configured (missing endpoint or api_key).")
        return []

    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": api_key,
    }
    params: list[tuple[str, str | bytes | int | float]] = [
        ("q", query),
        ("count", max_results),
        ("country", "us"),
        ("safesearch", "moderate"),
    ]

    try:
        with requests.Session() as s:
            resp = s.get(endpoint, headers=headers, params=params, timeout=_HTTP_TIMEOUT_SECS)
            resp.raise_for_status()
            data = resp.json()
    except requests.RequestException:
        logger.exception("Brave web search request failed")
        return []
    except ValueError:
        logger.exception("Brave web search: JSON decode failed")
        return []

    results: List[Dict[str, Any]] = []
    for item in (data.get("web", {}) or {}).get("results", [])[:max_results]:
        title = item.get("title") or ""
        url = item.get("url") or item.get("link") or ""
        desc = item.get("description") or item.get("snippet") or ""
        results.append(
            {
                "title": title,
                "href": url,
                "body": desc,
                "source": "brave",
            }
        )

    logger.info("Web search results count (brave): %d", len(results))
    return results


def search_web(query: str) -> List[Dict[str, Any]]:
    """
    Query web using configured backend and return normalized results:
    [{'title': str, 'href': str, 'body': str, 'source': str}, ...]
    """
    logger.info("Executing web search with the following query: %s", query)

    method = (config.search.method_name or "").lower()
    if method == "ddgs":
        return _search_with_ddgs(query)
    if method == "brave":
        return _search_with_brave(query)

    logger.warning("No valid search method specified ('%s'); returning empty list.", method)
    return []


def make_eval_prompt(system_prompt: str) -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("user", "Here is the result of a search from a search engine: {tool_output_raw}"),
            ("user", "{input}"),
        ]
    )


def fetch_parser_response(
    parser_model: ChatOpenAI,
    unstructured_output: str,
    pydantic_class_template: type[PyScrapeFetchReferences],
) -> PyScrapeFetchReferences:
    """
    Parse the output of the main LLM into a pydantic class template using a smaller LLM.
    """
    base_parser: PydanticOutputParser[PyScrapeFetchReferences] = PydanticOutputParser(
        pydantic_object=pydantic_class_template
    )
    meta_parser = OutputFixingParser.from_llm(parser=base_parser, llm=parser_model)
    parsed_output = meta_parser.parse(unstructured_output)
    assert isinstance(parsed_output, PyScrapeFetchReferences)  # helps type-checkers
    return parsed_output


def _generate_query_via_llm(
    model: ChatOpenAI, parser_model: ChatOpenAI, user_prompt: str, system_prompt: str
) -> Tuple[str, Any]:
    """
    Ask the LLM to produce a query string and post-parse it into PySearchPrompt.
    Returns (query_string, raw_model_response).
    """
    logger.info("Call LLM to generate a search query.")
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
    response = model.invoke(messages)

    logger.info("Call LLM to format the search query.")
    base_parser: PydanticOutputParser[PySearchPrompt] = PydanticOutputParser(pydantic_object=PySearchPrompt)
    meta_parser = OutputFixingParser.from_llm(parser=base_parser, llm=parser_model)
    parsed = meta_parser.parse(str(response.content))
    assert isinstance(parsed, PySearchPrompt)

    query = parsed.search_prompt.strip()
    if not query:
        logger.warning("LLM returned an empty search prompt; falling back to a template.")
        # fallback to a generic template if the LLM hiccups
        query = "{app} API documentation {ver}"
    return query, response


def _generate_query_via_preset(app: str, ver: str) -> Tuple[str, str]:
    """
    Produce a deterministic query from a string preset.
    Returns (query_string, preset_used)
    """
    preset = "{app} API documentation {ver}"
    query = preset.format(app=app, ver=ver)
    return query, preset


def query_and_search_candidate(
    model: ChatOpenAI,
    parser_model: ChatOpenAI,
    user_prompt: str,
    system_prompt: str,
    resource: Sequence[str],
) -> Tuple[List[Dict[str, Any]], Any, PySearchPrompt]:
    """
    Generate query via LLM, search, and return normalized results.
    """
    app, ver = resource[0], resource[1]
    logger.info("Running discovery for application='%s' version='%s'", app, ver)

    query, response = _generate_query_via_llm(model, parser_model, user_prompt, system_prompt)
    # The LLM produced query might include placeholders; patch if present.
    query = query.format(app=app, ver=ver)

    logger.info("Running web search with prompt: %s", query)
    search_output = search_web(query)

    # Return the structured prompt object for downstream logging
    search_prompt = PySearchPrompt(search_prompt=query)
    parsed_struct = search_prompt
    return search_output, response, parsed_struct


def query_and_search_candidate_preset(resource: Sequence[str]) -> Tuple[List[Dict[str, Any]], str, PySearchPrompt]:
    """
    Generate query via deterministic preset, search, and return normalized results.
    """
    app, ver = resource[0], resource[1]
    logger.info("Running discovery (preset) for application='%s' version='%s'", app, ver)

    query, preset_used = _generate_query_via_preset(app, ver)
    logger.info("Running web search with preset prompt: %s", query)
    search_output = search_web(query)

    search_prompt = PySearchPrompt(search_prompt=query)
    parsed_struct = search_prompt
    return search_output, preset_used, parsed_struct


def fetch_candidate_links_simplified(
    model: ChatOpenAI,
    eval_model: ChatOpenAI,
    parser_model: ChatOpenAI,
    user_prompt: str,
    user_prompt_eval: str,
    system_prompt: str,
    system_prompt_eval: str,
    resource: Sequence[str],
    pydantic_class_template: type[PyScrapeFetchReferences],
    llm_generated_search_query: bool,
) -> Tuple[Any, PySearchPrompt, List[Dict[str, Any]], Any, Optional[PyScrapeFetchReferences]]:
    """
    End-to-end: create query (LLM or preset), search, evaluate results with LLM,
    and parse the evaluator's output into a structured model.
    """
    if llm_generated_search_query:
        search_output, response, parsed_response = query_and_search_candidate(
            model=model,
            parser_model=parser_model,
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            resource=resource,
        )
    else:
        search_output, response, parsed_response = query_and_search_candidate_preset(resource)

    # Evaluate the tool output
    eval_prompt_template = make_eval_prompt(system_prompt_eval)
    chain = eval_prompt_template | eval_model
    eval_input = user_prompt_eval.format(resource[0], resource[1])
    eval_output: Any = chain.invoke(
        {"tool_output_raw": str(search_output), "input": eval_input},
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
            return response, parsed_response, search_output, eval_output, parsed_eval_output
        except Exception as exc:
            logger.info("Parsing attempt %d failed with error: %s", attempt + 1, exc)

    # Final fallback (None means parsing failed)
    return response, parsed_response, search_output, eval_output, None


async def _run_discovery_async(app_data: CandidateLinksInput, job_id: UUID) -> CandidateLinksOutput:
    """Run the discovery pipeline. Async wrapper to handle progress updates properly."""
    discovery_main_model = get_default_llm_small1()
    discovery_eval_model = get_default_llm_small2()
    discovery_parser_model = get_default_llm_small2()

    # fetch prompts
    user_prompt_fetch = get_discovery_fetch_user_prompt(app_data.application_name, app_data.application_version, 2)
    user_prompt_eval = get_discovery_eval_user_prompt(app_data.application_name, app_data.application_version, 1)

    system_prompt_fetch = get_discovery_fetch_sys_prompt(1)
    system_prompt_eval = get_discovery_eval_sys_prompt()

    try:
        update_job_progress(job_id, stage=JobStage.processing, message="Discovering candidate links")
    except Exception as exc:
        logger.info("update_job_progress failed (start): %s", exc)

    def tasks():
        return fetch_candidate_links_simplified(
            model=discovery_main_model,
            eval_model=discovery_eval_model,
            parser_model=discovery_parser_model,
            user_prompt=user_prompt_fetch,
            user_prompt_eval=user_prompt_eval,
            system_prompt=system_prompt_fetch,
            system_prompt_eval=system_prompt_eval,
            resource=[app_data.application_name, app_data.application_version or ""],
            pydantic_class_template=PyScrapeFetchReferences,
            llm_generated_search_query=app_data.llm_generated_search_query,
        )

    model_output, output_message, tool_output_raw, eval_output, parsed = await asyncio.to_thread(tasks)

    parsed_ref = cast(Optional[PyScrapeFetchReferences], parsed)
    if not parsed_ref:
        output: List[str] = []
        output_enriched: List[Dict[str, Any]] = []
    else:
        output = parsed_ref.urls_to_crawl
        logger.info("Selected urls to crawl next: %s", output)

        # Build enriched results by matching chosen URLs against the raw tool output.
        output_enriched = []
        try:
            # Index tool_output_raw by URL for O(1) lookups
            by_href = {str(item.get("href", "")): item for item in tool_output_raw if isinstance(item, dict)}
            for url in parsed_ref.urls_to_crawl:
                item = by_href.get(url)
                if item:
                    output_enriched.append(item)
                else:
                    # Fallback: scan values if the 'href' normalization didn't match exactly
                    item_scan = next((d for d in tool_output_raw if isinstance(d, dict) and url in d.values()), None)
                    if item_scan:
                        output_enriched.append(item_scan)
        except Exception as exc:
            logger.info("Failed to extract enriched reference list. Error: %s", exc)
            output_enriched = []

    logger.info("End of the discovery script.")
    return CandidateLinksOutput(candidate_links=output, candidate_links_enriched=output_enriched)


async def fetch_candidate_links(app_data: CandidateLinksInput, job_id: UUID) -> CandidateLinksOutput:
    """
    Generate a search query, execute it, and select the most relevant result using LLM.
    """
    return await _run_discovery_async(app_data, job_id)
