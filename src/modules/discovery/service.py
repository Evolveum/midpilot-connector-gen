# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple, cast
from uuid import UUID

from langchain_core.runnables.config import RunnableConfig
from langchain_openai import ChatOpenAI

from ...common.enums import JobStage
from ...common.jobs import update_job_progress
from ...common.langfuse import langfuse_handler
from ...common.llm import get_default_llm_small1, get_default_llm_small2
from .core.search import SearchResult, search_web
from .prompts.prompts import (
    get_discovery_eval_sys_prompt,
    get_discovery_eval_user_prompt,
    get_discovery_fetch_sys_prompt,
    get_discovery_fetch_user_prompt,
)
from .schema import CandidateLinksInput, CandidateLinksOutput, PyScrapeFetchReferences, PySearchPrompt
from .utils.llm_helpers import (
    fetch_parser_response,
    generate_query_via_llm,
    generate_query_via_preset,
    make_eval_prompt,
)

logger = logging.getLogger(__name__)


def query_and_search_candidate(
    model: ChatOpenAI,
    parser_model: ChatOpenAI,
    user_prompt: str,
    system_prompt: str,
    app: str,
    ver: str,
) -> Tuple[List[SearchResult], Any, PySearchPrompt]:
    """
    Generate query via LLM, search, and return normalized results.
    """
    logger.info("Running discovery for application='%s' version='%s'", app, ver)

    query, response, _ = generate_query_via_llm(model, parser_model, user_prompt, system_prompt)
    # The LLM produced query might include placeholders; patch if present.
    query = query.format(app=app, ver=ver)

    logger.info("Running web search with prompt: %s", query)
    search_output = search_web(query)

    # Return the structured prompt object for downstream logging
    search_prompt = PySearchPrompt(search_prompt=query)
    return search_output, response, search_prompt


def query_and_search_candidate_preset(app: str, ver: str) -> Tuple[List[SearchResult], str, PySearchPrompt]:
    """
    Generate query via deterministic preset, search, and return normalized results.
    """
    logger.info("Running discovery (preset) for application='%s' version='%s'", app, ver)

    query, preset_used = generate_query_via_preset(app, ver)
    logger.info("Running web search with preset prompt: %s", query)
    search_output = search_web(query)

    search_prompt = PySearchPrompt(search_prompt=query)
    return search_output, preset_used, search_prompt


def fetch_candidate_links_simplified(
    model: ChatOpenAI,
    eval_model: ChatOpenAI,
    parser_model: ChatOpenAI,
    user_prompt: str,
    user_prompt_eval: str,
    system_prompt: str,
    system_prompt_eval: str,
    app: str,
    ver: str,
    pydantic_class_template: type[PyScrapeFetchReferences],
    llm_generated_search_query: bool,
) -> Tuple[Any, PySearchPrompt, List[SearchResult], Any, Optional[PyScrapeFetchReferences]]:
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
            app=app,
            ver=ver,
        )
    else:
        search_output, response, parsed_response = query_and_search_candidate_preset(app, ver)

    # Evaluate the tool output
    eval_prompt_template = make_eval_prompt(system_prompt_eval)
    chain = eval_prompt_template | eval_model
    eval_input = user_prompt_eval.format(app, ver)
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

    app_version = app_data.application_version or ""

    def tasks():
        return fetch_candidate_links_simplified(
            model=discovery_main_model,
            eval_model=discovery_eval_model,
            parser_model=discovery_parser_model,
            user_prompt=user_prompt_fetch,
            user_prompt_eval=user_prompt_eval,
            system_prompt=system_prompt_fetch,
            system_prompt_eval=system_prompt_eval,
            app=app_data.application_name,
            ver=app_version,
            pydantic_class_template=PyScrapeFetchReferences,
            llm_generated_search_query=app_data.llm_generated_search_query,
        )

    _model_output, _output_message, tool_output_raw, _eval_output, parsed = await asyncio.to_thread(tasks)

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
