# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
The LLM call behind the object-class connector fix.

Mirrors the ``generation`` / ``core.generate_groovy`` split: the domain workflow
lives in ``connector_fix``, the single structured LLM pass lives here. This module
must not touch the session repository or the job scheduler.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.runnables.config import RunnableConfig

from src.config import config
from src.core.llm import build_structured_chain, raise_if_llm_unavailable, retry_on_transient_llm_error
from src.core.observability.langfuse import langfuse_handler
from src.documents.chunking import count_tokens
from src.jobs import append_job_error
from src.modules.codegen.errors import ConnectorFixContextTooLargeError
from src.modules.codegen.prompts.fix_prompts import (
    CONNECTOR_FIX_DOCUMENTATION_INSTRUCTION,
    CONNECTOR_FIX_DOCUMENTATION_SECTION,
    CONNECTOR_FIX_PREVIOUS_ATTEMPT_SECTION,
    get_connector_fix_system_prompt,
    get_connector_fix_user_prompt,
)
from src.modules.codegen.schema import ConnectorFixLLMResponse
from src.shared.enums import ApiType

logger = logging.getLogger(__name__)


def render_script_bundle(artifact_payloads: Sequence[Dict[str, Any]]) -> str:
    """
    Render the connector's scripts as tagged blocks.

    Tag-delimited rather than JSON: escaping fifty Groovy scripts into JSON strings
    costs a large share of the prompt budget and reads worse. The closing tag is a
    literal ``</script>``, which Groovy itself cannot produce.
    """
    blocks: List[str] = []
    for payload in artifact_payloads:
        attributes = [f'operationKey="{payload["operationKey"]}"', f'kind="{payload["kind"]}"']
        if payload.get("objectClass"):
            attributes.append(f'objectClass="{payload["objectClass"]}"')
        if payload.get("relationName"):
            attributes.append(f'relationName="{payload["relationName"]}"')
        if payload.get("intent"):
            attributes.append(f'intent="{payload["intent"]}"')
        blocks.append(f"<script {' '.join(attributes)}>\n{payload['code']}\n</script>")
    return "\n\n".join(blocks)


def render_previous_attempt(response: ConnectorFixLLMResponse) -> str:
    lines = [f"- {script.operation_key}: {script.reason}" for script in response.fixed_scripts]
    if response.analysis:
        lines.append(f"- analysis: {response.analysis}")
    return "\n".join(lines) if lines else "No scripts were proposed."


async def _enforce_prompt_token_budget(
    *,
    partial_variables: Dict[str, Any],
    prompt_vars: Dict[str, Any],
) -> None:
    """Estimate the complete rendered chat input before invoking the provider."""
    parser: PydanticOutputParser[Any] = PydanticOutputParser(pydantic_object=ConnectorFixLLMResponse)
    system_text = get_connector_fix_system_prompt.format(**partial_variables)
    user_text = get_connector_fix_user_prompt.format(**prompt_vars)
    rendered_input = f"{system_text}\n\n{parser.get_format_instructions()}\n\n{user_text}"
    input_tokens = await asyncio.to_thread(count_tokens, rendered_input)
    limit = config.codegen.fix_max_input_tokens
    if input_tokens > limit:
        raise ConnectorFixContextTooLargeError(input_tokens=input_tokens, limit=limit)


async def run_connector_fix_pass(
    *,
    artifact_payloads: Sequence[Dict[str, Any]],
    midpoint_errors: Sequence[str],
    protocol: ApiType,
    connection_target: str,
    dsl_documentation: str,
    job_id: UUID,
    documentation_query: str | None = None,
    documentation_chunks: str = "",
    previous_attempt: Optional[ConnectorFixLLMResponse] = None,
) -> ConnectorFixLLMResponse | None:
    """
    Run one fix pass and return the parsed structured output, or ``None`` on failure.

    Passing ``documentation_chunks`` and ``previous_attempt`` turns this into the
    escalation pass; nothing else differs between the two.

    :raises LLMUnavailableError: when the model backend is unreachable
    """
    is_escalation = bool(documentation_chunks)

    documentation_context = (
        CONNECTOR_FIX_DOCUMENTATION_SECTION.format(
            documentation_query=documentation_query or "", documentation_chunks=documentation_chunks
        )
        if is_escalation
        else ""
    )
    previous_attempt_section = (
        CONNECTOR_FIX_PREVIOUS_ATTEMPT_SECTION.format(previous_attempt=render_previous_attempt(previous_attempt))
        if is_escalation and previous_attempt is not None
        else ""
    )

    partial_variables = {
        "protocol": protocol.value,
        "connection_target": connection_target or "not recorded",
        "documentation_instruction": CONNECTOR_FIX_DOCUMENTATION_INSTRUCTION if is_escalation else "",
    }
    prompt_vars = {
        "midpoint_errors": json.dumps(list(midpoint_errors), ensure_ascii=False, indent=2),
        "operation_scripts": render_script_bundle(artifact_payloads),
        "dsl_documentation": dsl_documentation or "No bundled DSL reference is available for these operations.",
        "documentation_context": documentation_context,
        "previous_attempt": previous_attempt_section,
    }
    await _enforce_prompt_token_budget(partial_variables=partial_variables, prompt_vars=prompt_vars)

    chain = build_structured_chain(
        get_connector_fix_system_prompt,
        get_connector_fix_user_prompt,
        ConnectorFixLLMResponse,
        partial_variables=partial_variables,
    )

    run_name = "codegen.fix.documentation" if is_escalation else "codegen.fix"
    try:
        response = await retry_on_transient_llm_error(
            lambda: chain.ainvoke(
                prompt_vars,
                config=RunnableConfig(callbacks=[langfuse_handler], run_name=run_name),
            ),
            max_attempts=config.llm.transient_retry_attempts,
            base_delay=config.llm.transient_retry_base_delay_seconds,
            logger_prefix="[Codegen:Fix] ",
            context="fixing connector code",
        )
    except Exception as exc:
        raise_if_llm_unavailable(exc, context="fixing connector code")
        error_message = f"[Codegen:Fix] Fix pass failed: {exc}"
        logger.exception("[Codegen:Fix] Fix pass failed: %s", exc)
        await append_job_error(job_id, error_message)
        return None

    if not isinstance(response, ConnectorFixLLMResponse):
        error_message = f"[Codegen:Fix] Unexpected fix response type: {type(response).__name__}"
        logger.warning("[Codegen:Fix] Unexpected fix response type: %s", type(response).__name__)
        await append_job_error(job_id, error_message)
        return None

    logger.info(
        "[Codegen:Fix] Fix pass returned %d proposed script(s), needsDocumentation=%s",
        len(response.fixed_scripts),
        response.needs_documentation,
    )
    return response
