# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from __future__ import annotations

import logging
from typing import Any, Tuple

from langchain.output_parsers import OutputFixingParser
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from ..schema import PyScrapeFetchReferences, PySearchPrompt

logger = logging.getLogger(__name__)


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
    assert isinstance(parsed_output, pydantic_class_template)  # helps type-checkers
    return parsed_output


def generate_query_via_llm(
    model: ChatOpenAI,
    parser_model: ChatOpenAI,
    user_prompt: str,
    system_prompt: str,
    *,
    fallback_template: str = "{app} API documentation {version}",
) -> Tuple[str, Any, PySearchPrompt]:
    """
    Ask the LLM to produce a query string and post-parse it into PySearchPrompt.
    Returns (query_string, raw_model_response, parsed_prompt).
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
        query = fallback_template

    return query, response, parsed


def generate_query_via_preset(
    app: str, version: str, *, template: str = "{app} API/SCIM documentation for version {version}"
) -> Tuple[str, str]:
    """
    Produce a deterministic query from a string preset.
    Returns (query_string, preset_used).
    """
    preset = template
    query = preset.format(app=app, ver=version)
    return query, preset
