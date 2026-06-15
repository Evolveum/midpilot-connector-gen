# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.
import asyncio
import logging
import ssl
from typing import Any, Awaitable, Callable, Final, Literal, Optional, TypeVar, cast

import httpx
from langchain_classic.output_parsers import RetryWithErrorOutputParser
from langchain_core.output_parsers import BaseOutputParser, PydanticOutputParser
from langchain_core.prompts import BasePromptTemplate, ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnableLambda, RunnableParallel
from langchain_openai import ChatOpenAI
from openai import APIConnectionError, APITimeoutError
from pydantic import BaseModel

from src.config import ReasoningEffort, config

logger = logging.getLogger(__name__)

T = TypeVar("T")

# HTTP status codes that indicate a transient, retryable LLM provider failure.
TRANSIENT_LLM_STATUS_CODES: Final[frozenset[int]] = frozenset({408, 409, 429, 500, 502, 503, 504})

# Substrings (lowercased) in error messages that indicate a transient failure.
_TRANSIENT_LLM_MESSAGE_MARKERS: Final[tuple[str, ...]] = (
    "connection error",
    "gateway time-out",
    "gateway timeout",
    "temporarily unavailable",
    "service unavailable",
    "rate limit",
    "timed out",
    "timeout",
    "overloaded",
)


def is_transient_llm_error(exc: BaseException) -> bool:
    """
    Decide whether an LLM/provider exception is transient and worth retrying.

    Covers connection/timeout errors (e.g. ``openai.APIConnectionError`` whose message is
    only "Connection error."), retryable HTTP status codes, and known transient message markers.
    """
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True

    status_code = getattr(exc, "status_code", None)
    if status_code in TRANSIENT_LLM_STATUS_CODES:
        return True

    message = str(exc).lower()
    return any(marker in message for marker in _TRANSIENT_LLM_MESSAGE_MARKERS)


async def retry_on_transient_llm_error(
    func: Callable[[], Awaitable[T]],
    *,
    max_attempts: int,
    base_delay: float,
    logger_prefix: str = "",
    context: Optional[str] = None,
) -> T:
    """
    Invoke an async LLM call with bounded exponential backoff on transient failures.

    Non-transient errors (and the final attempt) are re-raised unchanged so callers
    keep full visibility into genuine failures.
    """
    attempts = max(1, max_attempts)
    delay = max(0.0, base_delay)

    for attempt in range(1, attempts + 1):
        try:
            return await func()
        except Exception as exc:
            if attempt >= attempts or not is_transient_llm_error(exc):
                raise

            wait = delay * (2 ** (attempt - 1))
            logger.warning(
                "%sTransient LLM failure%s; retrying attempt %s/%s in %.1fs: %s",
                logger_prefix,
                f" for {context}" if context else "",
                attempt + 1,
                attempts,
                wait,
                exc,
            )
            if wait:
                await asyncio.sleep(wait)

    raise RuntimeError("LLM retry loop exited unexpectedly")


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
