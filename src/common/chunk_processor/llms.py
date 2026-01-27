# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

from ...common.llm import get_default_llm, make_basic_chain
from .prompts import get_summary_prompts
from .schema import LlmChunkOutput

logger = logging.getLogger(__name__)


# For now not used, but may be useful later
def get_summary_result(content: str, type: str = "partial") -> str:
    """
    Run a summary LLM on the given content and return the summary.
    inputs:
    - prompts: tuple of (developer_prompt, user_prompt)
    - type: "partial" for partial summary, "full" for full summary
    returns:
    - summary string
    """
    llm = get_default_llm(temperature=1)

    prompts = get_summary_prompts(content, type)

    developer_prompt, user_prompt = prompts

    developer_message = SystemMessage(content=developer_prompt)
    developer_message.additional_kwargs = {"__openai_role__": "developer"}

    user_message = HumanMessage(content=user_prompt)

    llm_response = llm.invoke([developer_message, user_message])

    print(f"LLM raw output for {type} summary: " + str(llm_response))

    content_raw = llm_response.content
    if isinstance(content_raw, str):
        return content_raw
    else:
        # Handle list format
        return str(content_raw)


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

    llm_response = await llm.ainvoke([developer_message, user_message])

    logger.debug("[Scrape:LLM] LLM raw output for chunk processing: %s", str(llm_response)[:200])

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

    result = await chain.ainvoke({})

    return result
