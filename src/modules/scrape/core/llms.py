# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

from src.config import config
from src.core.llm import get_default_llm, make_basic_chain, raise_if_llm_unavailable, retry_on_transient_llm_error
from src.core.observability.langfuse import build_llm_config
from src.modules.scrape.schema import RelevantLinks

logger = logging.getLogger(__name__)


async def get_relevant_links_from_text(prompts: Tuple[str, str]) -> RelevantLinks | None:
    """
    Get relevant links from text using an LLM.

    :param prompts: Tuple containing the system and user prompts.
    :param app: Application name.
    :param app_version: Application version.
    :return: RelevantLinks object containing the list of relevant links, or None if an error occurs.
    """
    logger.debug("[Scrape:LLM] Starting LLM call for relevant links extraction")
    system_msg, user_msg = prompts

    llm = get_default_llm()

    parser: PydanticOutputParser = PydanticOutputParser(pydantic_object=RelevantLinks)
    parser_instructions = parser.get_format_instructions()

    system_message = SystemMessage(content=system_msg.format(parser_instructions=parser_instructions))
    user_message = HumanMessage(content=user_msg)

    chat_prompts = ChatPromptTemplate.from_messages(
        [
            system_message,
            user_message,
        ]
    )

    chain = make_basic_chain(
        prompt=chat_prompts,
        llm=llm,
        parser=parser,
    )

    try:
        result = await retry_on_transient_llm_error(
            lambda: chain.ainvoke({}, config=build_llm_config("Scrape:Links:SelectRelevant")),
            max_attempts=config.scrape_and_process.chunk_llm_retry_attempts,
            base_delay=config.scrape_and_process.chunk_llm_retry_base_delay_seconds,
            context="relevant-link extraction",
        )
        logger.debug("[Scrape:LLM] LLM call successful for relevant links extraction")
        return result
    except Exception as e:
        raise_if_llm_unavailable(e, context="extracting relevant links")
        logger.exception("[Scrape:LLM] Error invoking LLM for relevant links extraction")
        return None
