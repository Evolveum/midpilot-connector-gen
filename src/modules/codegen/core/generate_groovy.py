# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

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
from src.modules.codegen.repair import build_repair_prompt_vars
from src.modules.codegen.schema import CodegenRepairContext
from src.modules.codegen.utils.groovy_validation import validate_groovy_code
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
    Ask the LLM to generate Groovy code given attribute records.
    Defensive against LLM output shapes; returns a minimal scaffold on failure.
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
        logger.info("[Codegen:%s] %s Groovy for %s", logger_prefix, action, object_class)
        resp = await retry_on_transient_llm_error(
            lambda: chain.ainvoke(
                vars_payload,
                config=RunnableConfig(
                    callbacks=[langfuse_handler],
                    run_name=f"Codegen:{logger_prefix or 'Groovy'}",
                ),
            ),
            max_attempts=config.llm.transient_retry_attempts,
            base_delay=config.llm.transient_retry_base_delay_seconds,
            logger_prefix=f"[Codegen:{logger_prefix}] ",
            context=f"generating Groovy for {object_class}",
        )
        text = coerce_llm_text(resp).strip()
        if not text:
            logger.warning("[Codegen:%s] Empty LLM response for %s", logger_prefix, object_class)
            return f'objectClass("{object_class}") {{}}'
        code = strip_markdown_fences(text)
        validation_error = validate_groovy_code(code)
        if validation_error is not None:
            error_message = f"[Codegen:{logger_prefix}] Generated invalid Groovy: {validation_error}"
            logger.warning(error_message)
            await append_job_error(job_id, error_message)
            return f'objectClass("{object_class}") {{}}'
        return code

    except Exception as exc:
        raise_if_llm_unavailable(exc, context=f"generating code for {object_class}")
        error_message = f"[Codegen:{logger_prefix}] Generation failed: {exc}"
        logger.exception(error_message)
        await append_job_error(job_id, error_message)
        return f'objectClass("{object_class}") {{}}'
