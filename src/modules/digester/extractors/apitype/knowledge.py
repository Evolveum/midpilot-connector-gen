# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Documentation-free apiType signals backed by the LLM's own knowledge.

Given only the application name the user entered in discovery, a single LLM call per protocol
asks the model whether that product is known to expose a given provisioning protocol (and
whether it is gated behind a paid plan), without sending any documentation. SCIM and REST are
asked independently so each can evolve without affecting the other.

Each call is best-effort: when the feature is disabled, the name is empty, or the LLM call
fails, a non-supporting result is returned (rather than raising) so callers can safely fall
back to the other signals.
"""

import logging
from typing import cast

from langchain_core.runnables.config import RunnableConfig

from src.common.langfuse import langfuse_handler
from src.common.llm import build_structured_chain
from src.config import config
from src.modules.digester.extraction.llm_execution import invoke_llm
from src.modules.digester.prompts.apitype.knowledge_prompts import (
    get_api_type_knowledge_system_prompt,
    get_api_type_knowledge_user_prompt,
    get_rest_knowledge_system_prompt,
    get_rest_knowledge_user_prompt,
)
from src.modules.digester.schemas import ApiTypeSignalResult, RestSignalResult

logger = logging.getLogger(__name__)

_LOG_PREFIX = "[ApiType:Knowledge] "
_REST_LOG_PREFIX = "[ApiType:RestKnowledge] "


async def lookup_api_type_knowledge(application_name: str) -> ApiTypeSignalResult:
    """
    Ask the LLM, from its own knowledge, whether ``application_name`` supports SCIM.

    Returns a non-supporting result (rather than raising) when the feature is disabled,
    the name is empty, or the LLM call fails, so callers can safely fall back to the
    documentation-based and scim.cloud signals.
    """
    log_prefix = _LOG_PREFIX
    if not config.digester.apitype_scim_knowledge_enabled:
        return ApiTypeSignalResult()
    if not application_name or not application_name.strip():
        logger.info("%sNo application name provided; skipping knowledge lookup", log_prefix)
        return ApiTypeSignalResult()

    chain = build_structured_chain(
        get_api_type_knowledge_system_prompt,
        get_api_type_knowledge_user_prompt,
        ApiTypeSignalResult,
        user_role="human",
    )

    try:
        result = cast(
            ApiTypeSignalResult,
            await invoke_llm(
                chain,
                {"application_name": application_name},
                config=RunnableConfig(callbacks=[langfuse_handler], run_name="Digester:ApiTypeKnowledge"),
            ),
        )
    except Exception as exc:
        logger.warning("%sKnowledge lookup failed for '%s', skipping signal: %s", log_prefix, application_name, exc)
        return ApiTypeSignalResult()

    if not result:
        logger.warning("%sEmpty knowledge response for '%s'", log_prefix, application_name)
        return ApiTypeSignalResult()

    logger.info(
        "%s'%s' knowledge result: supports_scim=%s, api_types=%s, scim_availability=%s, required_plan=%s",
        log_prefix,
        application_name,
        result.supports_scim,
        [api_type.value for api_type in result.api_type],
        result.scim_availability.value,
        result.required_plan or "-",
    )
    return result


async def lookup_rest_knowledge(application_name: str) -> RestSignalResult:
    """
    Ask the LLM, from its own knowledge, whether ``application_name`` exposes a REST/OpenAPI API.

    Returns a non-supporting result (rather than raising) when the feature is disabled,
    the name is empty, or the LLM call fails, so callers can safely fall back to the
    documentation-based and web-search signals.
    """
    log_prefix = _REST_LOG_PREFIX
    if not config.digester.apitype_rest_knowledge_enabled:
        return RestSignalResult()
    if not application_name or not application_name.strip():
        logger.info("%sNo application name provided; skipping knowledge lookup", log_prefix)
        return RestSignalResult()

    chain = build_structured_chain(
        get_rest_knowledge_system_prompt,
        get_rest_knowledge_user_prompt,
        RestSignalResult,
        user_role="human",
    )

    try:
        result = cast(
            RestSignalResult,
            await invoke_llm(
                chain,
                {"application_name": application_name},
                config=RunnableConfig(callbacks=[langfuse_handler], run_name="Digester:RestKnowledge"),
            ),
        )
    except Exception as exc:
        logger.warning("%sKnowledge lookup failed for '%s', skipping signal: %s", log_prefix, application_name, exc)
        return RestSignalResult()

    if not result:
        logger.warning("%sEmpty knowledge response for '%s'", log_prefix, application_name)
        return RestSignalResult()

    logger.info(
        "%s'%s' knowledge result: supports_rest=%s, availability=%s, required_plan=%s",
        log_prefix,
        application_name,
        result.supports_rest,
        result.availability.value,
        result.required_plan or "-",
    )
    return result
