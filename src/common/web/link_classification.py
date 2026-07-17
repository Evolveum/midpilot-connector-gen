# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""LLM-backed classification of web links, shared by the discovery and scrape modules.

Callers own their prompts; this module owns the shared output contract, the role/reasoning
policy and the transient-retry behaviour, so the two modules no longer depend on each other's
internals.
"""

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables.config import RunnableConfig

from src.common.langfuse import langfuse_handler
from src.common.llm import get_default_llm, make_basic_chain, retry_on_transient_llm_error
from src.common.web.schemas import IrrelevantLinks
from src.config import ReasoningEffort, config

_LINK_FILTER_REASONING_CAP: ReasoningEffort = "medium"
_LINK_FILTER_MAX_ATTEMPTS = 3
_LINK_FILTER_BASE_DELAY_SECONDS = 1.0


def _link_filter_reasoning_effort() -> ReasoningEffort | None:
    return _LINK_FILTER_REASONING_CAP if config.llm.reasoning_effort is not None else None


async def classify_irrelevant_links(prompts: tuple[str, str]) -> IrrelevantLinks:
    """Ask the LLM which of the supplied links are irrelevant.

    The pre-rendered ``(system, user)`` prompts are wrapped in concrete message objects (not
    string templates), so any literal ``{`` / ``}`` in the link data stays as text instead of
    being parsed as a template variable. Transient provider failures are retried via
    :func:`retry_on_transient_llm_error`; malformed structured output is repaired by the chain's
    retry parser.

    :param prompts: Pre-rendered ``(system, user)`` prompts produced by the calling module.
    :return: The parsed :class:`IrrelevantLinks`.
    :raises Exception: If the call keeps failing after the retry budget is exhausted.
    """
    system_prompt, user_prompt = prompts

    system_message = SystemMessage(content=system_prompt)
    system_message.additional_kwargs = {"__openai_role__": "developer"}
    user_message = HumanMessage(content=user_prompt)
    user_message.additional_kwargs = {"__openai_role__": "user"}

    chain = make_basic_chain(
        prompt=ChatPromptTemplate.from_messages([system_message, user_message]),
        llm=get_default_llm(reasoning_effort=_link_filter_reasoning_effort()),
        parser=PydanticOutputParser(pydantic_object=IrrelevantLinks),
    )

    return await retry_on_transient_llm_error(
        lambda: chain.ainvoke(
            {}, config=RunnableConfig(callbacks=[langfuse_handler], run_name="Web:ClassifyIrrelevantLinks")
        ),
        max_attempts=_LINK_FILTER_MAX_ATTEMPTS,
        base_delay=_LINK_FILTER_BASE_DELAY_SECONDS,
        context="irrelevant-link filter",
    )
