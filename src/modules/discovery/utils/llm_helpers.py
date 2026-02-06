# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from __future__ import annotations

import logging
from typing import Any, List, Tuple

from langchain.output_parsers import OutputFixingParser
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from ..schema import PyScrapeFetchReferences, PySearchPrompts

logger = logging.getLogger(__name__)

DEFAULT_FALLBACK_TEMPLATES: list[str] = [
    "{app} API documentation {version}",
    "{app} developer API docs {version}",
    "{app} OpenAPI swagger {version}",
    "{app} REST API reference {version}",
    "{app} SCIM documentation {version}",
]


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
    """Parse the evaluator output into the pydantic_class_template."""
    base_parser: PydanticOutputParser[PyScrapeFetchReferences] = PydanticOutputParser(
        pydantic_object=pydantic_class_template
    )
    meta_parser = OutputFixingParser.from_llm(parser=base_parser, llm=parser_model)
    parsed_output = meta_parser.parse(unstructured_output)
    assert isinstance(parsed_output, pydantic_class_template)  # helps type-checkers
    return parsed_output


def _parse_search_prompts(parser_model: ChatOpenAI, raw_text: str) -> PySearchPrompts:
    base_parser: PydanticOutputParser[PySearchPrompts] = PydanticOutputParser(pydantic_object=PySearchPrompts)
    meta_parser = OutputFixingParser.from_llm(parser=base_parser, llm=parser_model)
    parsed = meta_parser.parse(raw_text)
    assert isinstance(parsed, PySearchPrompts)
    return parsed


def generate_queries_via_llm(
    *,
    model: ChatOpenAI,
    parser_model: ChatOpenAI,
    user_prompt: str,
    system_prompt: str,
    num_queries: int,
    fallback_templates: List[str] | None = None,
) -> Tuple[List[str], Any, PySearchPrompts]:
    """Ask the LLM to produce multiple search queries.

    Returns:
        (queries, raw_model_response, parsed_prompts)
    """
    logger.info("Call LLM to generate %d search queries.", num_queries)

    enriched_user_prompt = f"{user_prompt}\n\nReturn exactly {num_queries} distinct queries."

    messages = [SystemMessage(content=system_prompt), HumanMessage(content=enriched_user_prompt)]
    response = model.invoke(messages)

    logger.info("Parse LLM output into structured search prompts.")
    parsed = _parse_search_prompts(parser_model, str(response.content))

    if parsed.search_prompts:
        queries = [q.strip() for q in parsed.search_prompts if isinstance(q, str) and q.strip()]
    else:
        queries = []

    if len(queries) < num_queries:
        logger.warning(
            "LLM returned %d/%d queries; adding deterministic fallbacks.",
            len(queries),
            num_queries,
        )

        templates = fallback_templates or DEFAULT_FALLBACK_TEMPLATES
        for tmpl in templates:
            if len(queries) >= num_queries:
                break
            q = tmpl.strip()
            if q and q not in queries:
                queries.append(q)

    queries = [q for q in queries if isinstance(q, str) and q.strip()][:num_queries]
    if not queries:
        queries = (fallback_templates or DEFAULT_FALLBACK_TEMPLATES)[:num_queries]

    return queries, response, parsed


def generate_queries_via_preset(
    app: str,
    version: str,
    *,
    num_queries: int,
    templates: List[str] | None = None,
) -> Tuple[List[str], str, PySearchPrompts]:
    """Produce multiple deterministic queries from string presets."""
    used_templates = templates or DEFAULT_FALLBACK_TEMPLATES
    queries: List[str] = []

    for tmpl in used_templates:
        if len(queries) >= num_queries:
            break
        try:
            q = tmpl.format(app=app, version=version).strip()
        except Exception:
            q = tmpl.strip()
        if q and q not in queries:
            queries.append(q)

    parsed = PySearchPrompts(search_prompts=queries)
    preset_used = " | ".join(used_templates[:num_queries])
    return queries, preset_used, parsed
