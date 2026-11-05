# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""The scraper's LLM decisions: page assessment before a page is saved, and selection of the links to follow.

A product or version mismatch counts only with evidence quoted from the page; a ``"no"`` whose quote is not
on the page becomes ``"unclear"``, so a guess never keeps a page from being saved or used. An LLM outage is
raised as ``LLMUnavailableError``; any other failure is raised to the caller, which records it on the job.
"""

import asyncio
import logging
from collections.abc import Sequence
from typing import TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from src.config import config
from src.core.llm import get_default_llm, make_basic_chain, raise_if_llm_unavailable, retry_on_transient_llm_error
from src.core.observability.langfuse import build_llm_config
from src.documents import PageAssessmentMetadata
from src.documents.chunking import encoding
from src.documents.normalize import comparable_quote_text, is_quoted_from
from src.documents.schemas import MatchVerdict
from src.integrations.web.page_links import PageLink
from src.modules.scrape.prompts.prompts import get_link_selection_prompts, get_page_assessment_prompts
from src.modules.scrape.schema import LinkSelection, PageAssessment

logger = logging.getLogger(__name__)

OutputT = TypeVar("OutputT", bound=BaseModel)
_TOKEN_ENCODING = "cl100k_base"


async def assess_page(
    *, url: str, title: str, content: str, app: str, version: str | None
) -> tuple[bool, PageAssessmentMetadata]:
    """Whether to keep a page and what it documents, with the verdicts' evidence verified against the page.

    ``version`` is the concrete version the scrape targets, or None, in which case the version is not assessed.
    """
    # Tokenizing a long page is CPU-bound; keep it off the event loop shared with the API.
    page_text = await asyncio.to_thread(_cut_to_tokens, content, config.scrape_and_process.assessment_content_tokens)
    parser: PydanticOutputParser = PydanticOutputParser(pydantic_object=PageAssessment)
    system_message, user_message = get_page_assessment_prompts(
        app=app,
        version=version,
        url=url,
        title=title,
        content=page_text,
        parser_instructions=parser.get_format_instructions(),
    )
    output = await _invoke(
        system_message, user_message, parser, run_name="Scrape:Pages:Assess", context="page assessment"
    )
    evidence_source = f"{title}\n{page_text}"
    return output.keep, verify_assessment(output, evidence_source, url=url, version_assessed=version is not None)


async def select_links(links: Sequence[PageLink], *, app: str, version: str | None) -> list[PageLink]:
    """The links worth following, chosen by index from at most ``max_links_per_assessment`` candidates."""
    parser: PydanticOutputParser = PydanticOutputParser(pydantic_object=LinkSelection)
    system_message, user_message = get_link_selection_prompts(
        app=app, version=version, links=links, parser_instructions=parser.get_format_instructions()
    )
    output = await _invoke(
        system_message, user_message, parser, run_name="Scrape:Links:Select", context="link selection"
    )
    indices = list(dict.fromkeys(output.selected))
    invalid = [index for index in indices if not 0 <= index < len(links)]
    if invalid:
        logger.warning(
            "[Scrape:Links] Link selection returned %s indices outside the list, ignoring them", len(invalid)
        )
    return [links[index] for index in indices if 0 <= index < len(links)]


def verify_assessment(
    output: PageAssessment, page_text: str, *, url: str, version_assessed: bool
) -> PageAssessmentMetadata:
    """Assessment metadata whose evidence is quoted from ``page_text`` (the page title and the content the LLM
    read); an unsupported ``"no"`` becomes ``"unclear"``."""
    comparable = comparable_quote_text(page_text)
    product_match, product_evidence = _verified(output.product_match, output.product_evidence, comparable)
    if version_assessed:
        version_match, version_evidence = _verified(output.version_match, output.version_evidence, comparable)
    else:
        version_match, version_evidence = "unclear", None
    unsupported = [
        aspect
        for aspect, claimed, verified in (
            ("product", output.product_match, product_match),
            ("version", output.version_match if version_assessed else "unclear", version_match),
        )
        if claimed == "no" and verified != "no"
    ]
    if unsupported:
        logger.info(
            "[Scrape:Assess] %s: %s mismatch without a quote from the page, treated as unclear",
            url,
            " and ".join(unsupported),
        )
    return PageAssessmentMetadata(
        role=output.role,
        product_match=product_match,
        product_evidence=product_evidence,
        version_match=version_match,
        version_evidence=version_evidence,
        covers=output.covers,
        object_classes=output.mentioned_object_classes,
    )


def _verified(verdict: MatchVerdict, evidence: str, comparable_page: str) -> tuple[MatchVerdict, str | None]:
    """The verdict and its evidence when the evidence is quoted from the page; a ``"no"`` needs such a quote."""
    if evidence and is_quoted_from(evidence, comparable_page):
        return verdict, evidence
    return ("unclear" if verdict == "no" else verdict), None


def _cut_to_tokens(text: str, max_tokens: int) -> str:
    """``text`` cut after ``max_tokens`` tokens; only a guard for the LLM context window."""
    token_encoding = encoding(_TOKEN_ENCODING)
    tokens = token_encoding.encode(text)
    return text if len(tokens) <= max_tokens else token_encoding.decode(tokens[:max_tokens])


async def _invoke(
    system_message: str,
    user_message: str,
    parser: PydanticOutputParser[OutputT],
    *,
    run_name: str,
    context: str,
) -> OutputT:
    # Message objects are not templates, so braces in page content and URLs need no escaping.
    prompt = ChatPromptTemplate.from_messages(
        [SystemMessage(content=system_message), HumanMessage(content=user_message)]
    )
    chain = make_basic_chain(prompt=prompt, llm=get_default_llm(), parser=parser)
    try:
        return await retry_on_transient_llm_error(
            lambda: chain.ainvoke({}, config=build_llm_config(run_name)),
            max_attempts=config.scrape_and_process.chunk_llm_retry_attempts,
            base_delay=config.scrape_and_process.chunk_llm_retry_base_delay_seconds,
            context=context,
        )
    except Exception as exc:
        raise_if_llm_unavailable(exc, context=context)
        raise
