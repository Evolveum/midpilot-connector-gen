# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from src.config import config
from src.core.llm import (
    get_default_llm,
    make_basic_chain,
    raise_if_llm_unavailable,
    retry_on_transient_llm_error,
)
from src.core.observability.langfuse import build_llm_config
from src.jobs import append_job_error, update_job_progress
from src.modules.codegen.core.tracing import generation_run_name
from src.modules.codegen.prompts.validation_error_prompts import build_rejected_artifact_block
from src.modules.codegen.repair import NO_CODE_GENERATED, NO_REPAIR_GENERATED, build_repair_prompt_vars
from src.modules.codegen.schema import CodegenRepairContext
from src.modules.codegen.utils.connector_code_validation import collect_connector_code_errors
from src.modules.codegen.utils.postprocess import coerce_llm_text, strip_markdown_fences
from src.shared.enums import JobStage

logger = logging.getLogger(__name__)


async def generate_groovy(
    records: List[Dict[str, Any]],
    object_class: str,
    system_prompt: str,
    user_prompt: str,
    job_id: UUID,
    *,
    run_name: str,
    operation_name: str,
    extra_prompt_vars: Optional[Dict[str, Any]] = None,
    repair_context: Optional[CodegenRepairContext] = None,
) -> str:
    """
    Ask the LLM to generate a connector artifact given attribute records.

    Despite its name (kept to avoid churn in every call site), the output may be
    declarative YAML or Groovy - see
    ``src.modules.codegen.prompts.declarative_format_prompts``. Defensive against
    LLM output shapes; returns empty code when no artifact is accepted, preserving
    supplied code during repair. An invalid artifact is retried exactly once with its
    validation errors - the single-call counterpart of the chunked carry-over.

    ``run_name`` identifies the operation in traces and job errors;
    ``operation_name`` is the human-readable progress label.
    """
    df_json = json.dumps(records, ensure_ascii=False)
    llm = get_default_llm()

    prompt = ChatPromptTemplate.from_messages(
        [("system", system_prompt), ("user", user_prompt + "{validation_errors}")]
    )
    chain = make_basic_chain(prompt, llm, StrOutputParser())

    vars_payload: Dict[str, Any] = {"object_class": object_class, "records_json": df_json}
    if extra_prompt_vars:
        vars_payload.update(extra_prompt_vars)
    vars_payload.update(build_repair_prompt_vars(repair_context))

    llm_config = build_llm_config(generation_run_name(run_name, repair=repair_context is not None))

    async def _generate(validation_errors: str) -> str:
        resp = await retry_on_transient_llm_error(
            lambda: chain.ainvoke(
                {**vars_payload, "validation_errors": validation_errors},
                config=llm_config,
            ),
            max_attempts=config.llm.transient_retry_attempts,
            base_delay=config.llm.transient_retry_base_delay_seconds,
            context=f"{run_name} for {object_class}",
        )
        return strip_markdown_fences(coerce_llm_text(resp))

    try:
        action = "Repairing" if repair_context else "Generating"
        await update_job_progress(job_id, stage=JobStage.generating, message=f"{action} {operation_name}")
        logger.info("[Codegen:Generation] %s connector code for %s", action, object_class)
        code = await _generate("")
        if not code:
            logger.info("[Codegen:Generation] Empty LLM response for %s", object_class)
            await append_job_error(job_id, NO_REPAIR_GENERATED if repair_context else NO_CODE_GENERATED)
            return (repair_context.current_script or "") if repair_context else ""
        errors = await asyncio.to_thread(collect_connector_code_errors, code)
        if errors:
            logger.warning("[Codegen:Generation] Generated invalid output, retrying once: %s", "; ".join(errors))
            retried = await _generate(build_rejected_artifact_block(code, errors))
            # An empty retry leaves the first candidate and its errors as the reported failure.
            if retried:
                code = retried
                errors = await asyncio.to_thread(collect_connector_code_errors, code)
        if errors:
            error_message = f"[{run_name}] Generated invalid output: {'; '.join(errors)}"
            logger.warning("[Codegen:Generation] Generated invalid output after retry: %s", "; ".join(errors))
            await append_job_error(job_id, error_message)
            await append_job_error(job_id, NO_REPAIR_GENERATED if repair_context else NO_CODE_GENERATED)
            return (repair_context.current_script or "") if repair_context else ""
        return code

    except Exception as exc:
        raise_if_llm_unavailable(exc, context=f"generating code for {object_class}")
        error_message = f"[{run_name}] Generation failed: {exc}"
        logger.exception("[Codegen:Generation] Generation failed")
        await append_job_error(job_id, error_message)
        await append_job_error(job_id, NO_REPAIR_GENERATED if repair_context else NO_CODE_GENERATED)
        return (repair_context.current_script or "") if repair_context else ""
