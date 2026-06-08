# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.
import ssl
from typing import Any, Final, Literal, cast

import httpx
from langchain_classic.output_parsers import RetryWithErrorOutputParser
from langchain_core.output_parsers import BaseOutputParser, PydanticOutputParser
from langchain_core.prompts import BasePromptTemplate, ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnableLambda, RunnableParallel
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from src.config import ReasoningEffort, config


def _build_llm_verify_config(ca_cert_file: str | None) -> bool | ssl.SSLContext:
    if not ca_cert_file:
        return True

    ssl_context = httpx.create_ssl_context(verify=True)
    ssl_context.load_verify_locations(cafile=ca_cert_file)
    return ssl_context


_DEFAULT_REASONING_EFFORT: Final = object()


def get_default_llm(
    temperature: float = 1,
    reasoning_effort: ReasoningEffort | None | object = _DEFAULT_REASONING_EFFORT,
) -> ChatOpenAI:
    """
    Create and return a ChatOpenAI LLM instance with default parameters.

    :param temperature: Sampling temperature for the LLM (controls randomness).
    :param reasoning_effort: Optional reasoning effort override for models that support it.
        Omit to use config.llm.reasoning_effort. Pass None to disable reasoning effort for this call.
    :return: Configured ChatOpenAI instance.
    """

    http_client = httpx.AsyncClient(verify=_build_llm_verify_config(config.llm.ca_cert_file))
    selected_reasoning_effort = (
        config.llm.reasoning_effort
        if reasoning_effort is _DEFAULT_REASONING_EFFORT
        else cast(ReasoningEffort | None, reasoning_effort)
    )
    llm_kwargs: dict[str, Any] = {
        "api_key": config.llm.openai_api_key,
        "base_url": config.llm.openai_api_base,
        "model": config.llm.model_name,
        "timeout": config.llm.request_timeout,
        "temperature": temperature,
        "extra_body": {"provider": {"order": config.llm.provider_order}},
        "http_async_client": http_client,
        "max_retries": 0,
    }
    if selected_reasoning_effort is not None:
        llm_kwargs["reasoning_effort"] = selected_reasoning_effort

    return ChatOpenAI(**llm_kwargs)


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


def build_structured_chain(
    system_prompt: str,
    user_prompt: str,
    response_model: type[BaseModel],
    *,
    llm: ChatOpenAI | None = None,
    partial_variables: dict[str, Any] | None = None,
    user_role: Literal["human", "user"] = "user",
) -> Runnable:
    """Build a default LLM chain with a Pydantic structured-output parser."""
    parser: PydanticOutputParser[Any] = PydanticOutputParser(pydantic_object=response_model)
    prompt_variables = {"format_instructions": parser.get_format_instructions()}
    if partial_variables:
        prompt_variables.update(partial_variables)
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", f"{system_prompt}\n\n{{format_instructions}}"),
            (user_role, user_prompt),
        ]
    ).partial(**prompt_variables)
    return make_basic_chain(prompt, llm or get_default_llm(), parser)
