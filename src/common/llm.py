# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.
import ssl

import httpx
from langchain_classic.output_parsers import RetryWithErrorOutputParser
from langchain_core.output_parsers import BaseOutputParser
from langchain_core.prompts import BasePromptTemplate
from langchain_core.runnables import Runnable, RunnableLambda, RunnableParallel
from langchain_openai import ChatOpenAI

from src.config import config


def _build_llm_verify_config(ca_cert_file: str | None) -> bool | ssl.SSLContext:
    if not ca_cert_file:
        return True

    ssl_context = httpx.create_ssl_context(verify=True)
    ssl_context.load_verify_locations(cafile=ca_cert_file)
    return ssl_context


def get_default_llm(temperature: float = 1, reasoning_effort: str = "high") -> ChatOpenAI:
    """
    Create and return a ChatOpenAI LLM instance with default parameters.

    :param temperature: Sampling temperature for the LLM (controls randomness).
    :param reasoning_effort: Reasoning effort level for the LLM.
    :return: Configured ChatOpenAI instance.
    """

    http_client = httpx.AsyncClient(verify=_build_llm_verify_config(config.llm.ca_cert_file))
    return ChatOpenAI(
        api_key=config.llm.openai_api_key,
        base_url=config.llm.openai_api_base,
        model=config.llm.model_name,
        timeout=config.llm.request_timeout,
        temperature=temperature,
        extra_body={"provider": {"order": config.llm.provider_order}},
        reasoning_effort=reasoning_effort,
        http_async_client=http_client,
        max_retries=0,
    )


def make_basic_chain(prompt: BasePromptTemplate, llm: ChatOpenAI, parser: BaseOutputParser) -> Runnable:
    """
    Creates a basic processing chain that combines a prompt template, a language model, and an output parser.

    :param prompt: The template for generating prompts.
    :param llm: The language model used for generating completions.
    :param parser: The parser for processing the output.
    :return: A runnable chain that processes input through the prompt, language model, and parser.
    """

    async def parse_with_retry(param):
        return await retry_parser.aparse_with_prompt(param["completion"].content, param["prompt_value"])

    completion_chain = prompt | llm
    retry_parser = RetryWithErrorOutputParser.from_llm(parser=parser, llm=llm)

    chain = RunnableParallel(completion=completion_chain, prompt_value=prompt) | RunnableLambda(parse_with_retry)

    return chain
