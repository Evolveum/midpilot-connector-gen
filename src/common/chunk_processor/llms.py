# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables.config import RunnableConfig

from src.common.chunk_processor.schema import LlmChunkOutput
from src.common.langfuse import langfuse_handler
from src.common.llm import get_default_llm, make_basic_chain, retry_on_transient_llm_error
from src.config import config

logger = logging.getLogger(__name__)


async def get_llm_processed_chunk(prompts: tuple[str, str]) -> LlmChunkOutput:
    """
    Use LLM to generate summary, tags and category for a chunk.

    inputs:
    - prompts: tuple of (developer_prompt, user_prompt)
    - app: application name
    - app_version: application version
    returns:
    - LlmChunkOutput object containing summary, tags and category

    """
    logger.debug("[Scrape:LLM] Starting LLM call for chunk processing")
    llm = get_default_llm(temperature=0.7)

    developer_msg, user_msg = prompts

    developer_message = SystemMessage(content=developer_msg)
    developer_message.additional_kwargs = {"__openai_role__": "developer"}

    user_message = HumanMessage(content=user_msg)
    user_message.additional_kwargs = {"__openai_role__": "user"}

    prompt = ChatPromptTemplate.from_messages(
        [
            developer_message,
            user_message,
        ]
    )

    chain = make_basic_chain(
        prompt=prompt,
        llm=llm,
        parser=PydanticOutputParser(pydantic_object=LlmChunkOutput),
    )

    result = await retry_on_transient_llm_error(
        lambda: chain.ainvoke({}, config=RunnableConfig(callbacks=[langfuse_handler], run_name="Scrape:ProcessChunk")),
        max_attempts=config.scrape_and_process.chunk_llm_retry_attempts,
        base_delay=config.scrape_and_process.chunk_llm_retry_base_delay_seconds,
        logger_prefix="[Scrape:LLM] ",
        context="chunk processing",
    )

    logger.debug("[Scrape:LLM] Finished LLM call for chunk processing with result: %s", result)

    return result
