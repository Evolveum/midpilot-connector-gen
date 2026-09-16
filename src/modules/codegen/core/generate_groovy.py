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
from langchain_core.runnables.config import RunnableConfig

from src.config import config
from src.core.llm import (
    get_default_llm,
    make_basic_chain,
    raise_if_llm_unavailable,
    retry_on_transient_llm_error,
)
from src.core.observability.langfuse import langfuse_handler
from src.jobs import append_job_error, update_job_progress
from src.modules.codegen.repair import NO_CODE_GENERATED, NO_REPAIR_GENERATED, build_repair_prompt_vars
from src.modules.codegen.schema import CodegenRepairContext
from src.modules.codegen.utils.connector_code_validation import validate_connector_code
from src.modules.codegen.utils.postprocess import coerce_llm_text, strip_markdown_fences
from src.shared.enums import JobStage

logger = logging.getLogger(__name__)


async def generate_groovy(
    records: List[Dict[str, Any]],
    object_class: str,
    system_prompt: str,
    user_prompt: str,
    job_id: UUID,
    logger_prefix: str = "",
    extra_prompt_vars: Optional[Dict[str, Any]] = None,
    repair_context: Optional[CodegenRepairContext] = None,
) -> str:
    """
    Ask the LLM to generate a connector artifact given attribute records.

    Despite its name (kept for the deprecated ConnID prompt and to avoid churn in every native-schema
    call site), the output may be declarative YAML or Groovy - see
    ``src.modules.codegen.prompts.declarative_format_prompts``. Defensive against LLM output shapes;
    returns empty code when no artifact is accepted, preserving supplied code during repair.
    """
    df_json = json.dumps(records, ensure_ascii=False)
    llm = get_default_llm()

    prompt = ChatPromptTemplate.from_messages([("system", system_prompt), ("human", user_prompt)])
    chain = make_basic_chain(prompt, llm, StrOutputParser())

    vars_payload: Dict[str, Any] = {"object_class": object_class, "records_json": df_json}
    if extra_prompt_vars:
        vars_payload.update(extra_prompt_vars)
    vars_payload.update(build_repair_prompt_vars(repair_context))

    try:
        action = "Repairing" if repair_context else "Generating"
        await update_job_progress(job_id, stage=JobStage.generating, message=f"{action} {logger_prefix or 'code'}")
        logger.info("[Codegen:Generation] %s connector code for %s", action, object_class)
        resp = await retry_on_transient_llm_error(
            lambda: chain.ainvoke(
                vars_payload,
                config=RunnableConfig(
                    callbacks=[langfuse_handler],
                    run_name=f"Codegen:{logger_prefix or 'Generation'}",
                ),
            ),
            max_attempts=config.llm.transient_retry_attempts,
            base_delay=config.llm.transient_retry_base_delay_seconds,
            logger_prefix=f"[Codegen:{logger_prefix}] ",
            context=f"generating connector code for {object_class}",
        )
        code = strip_markdown_fences(coerce_llm_text(resp))
        if not code:
            logger.info("[Codegen:Generation] Empty LLM response for %s", object_class)
            await append_job_error(job_id, NO_REPAIR_GENERATED if repair_context else NO_CODE_GENERATED)
            return (repair_context.current_script or "") if repair_context else ""
        validation_error = await asyncio.to_thread(validate_connector_code, code)
        if validation_error is not None:
            error_message = f"[Codegen:{logger_prefix}] Generated invalid output: {validation_error}"
            logger.warning("[Codegen:Generation] Generated invalid output: %s", validation_error)
            await append_job_error(job_id, error_message)
            await append_job_error(job_id, NO_REPAIR_GENERATED if repair_context else NO_CODE_GENERATED)
            return (repair_context.current_script or "") if repair_context else ""
        return code

    except Exception as exc:
        raise_if_llm_unavailable(exc, context=f"generating code for {object_class}")
        error_message = f"[Codegen:{logger_prefix}] Generation failed: {exc}"
        logger.exception("[Codegen:Generation] Generation failed")
        await append_job_error(job_id, error_message)
        await append_job_error(job_id, NO_REPAIR_GENERATED if repair_context else NO_CODE_GENERATED)
        return (repair_context.current_script or "") if repair_context else ""
