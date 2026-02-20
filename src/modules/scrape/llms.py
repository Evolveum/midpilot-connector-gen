# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables.config import RunnableConfig

from ...common.langfuse import langfuse_handler
from ...common.llm import get_default_llm, make_basic_chain
from .schema import IrrelevantLinks

logger = logging.getLogger(__name__)


async def get_irrelevant_llm_response(prompts: tuple[str, str], max_retries: int = 3) -> IrrelevantLinks | None:
    """
    Create and return a ChatOpenAI LLM instance configured for filtering irrelevant links.

    :param links: List of links to evaluate.
    :param app: Application name.
    :param app_version: Application version.
    :return: Configured ChatOpenAI instance or None if failed.
    """
    logger.debug("[LLM] Starting LLM call for irrelevant links filtering")
    developer_msg, user_msg = prompts

    llm = get_default_llm(temperature=1, reasoning_effort="medium")

    developer_message = SystemMessage(content=developer_msg)
    developer_message.additional_kwargs = {"__openai_role__": "developer"}

    user_message = HumanMessage(content=user_msg)
    user_message.additional_kwargs = {"__openai_role__": "user"}

    req_num = 0
    done = False
    chat_prompts = ChatPromptTemplate.from_messages(
        [
            developer_message,
            user_message,
        ]
    )

    chain = make_basic_chain(
        prompt=chat_prompts,
        llm=llm,
        parser=PydanticOutputParser(pydantic_object=IrrelevantLinks),
    )

    result: IrrelevantLinks | None = None

    while req_num < max_retries and not done:
        try:
            result = await chain.ainvoke({}, config=RunnableConfig(callbacks=[langfuse_handler]))
            done = True
            logger.debug("[LLM] LLM call successful on attempt %s", req_num + 1)
        except Exception as e:
            logger.error("[LLM] Error invoking LLM (attempt %s/%s): %s", req_num + 1, max_retries, e)
            req_num += 1

    if not done:
        logger.error("[LLM] Failed to get LLM response after %s retries", max_retries)
        raise Exception("Failed to get LLM response after maximum retries")

    return result
