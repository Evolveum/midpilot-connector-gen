# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Any, Tuple
from uuid import UUID

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables.config import RunnableConfig

from src.common.chunks import normalize_to_text
from src.common.enums import JobStage
from src.common.jobs import append_job_error, update_job_progress
from src.common.langfuse import langfuse_handler
from src.common.llm import get_default_llm, make_basic_chain

from ..prompts.info_about_schema_prompts import get_info_system_prompt, get_info_user_prompt
from ..schema import InfoMetadata, InfoResponse
from ..utils.metadata_helper import extract_summary_and_tags

logger = logging.getLogger(__name__)


async def extract_info_metadata(
    schema: str,
    job_id: UUID,
    doc_id: UUID,
    initial_aggregated: Any | None = None,
    doc_metadata: dict[str, Any] | None = None,
) -> Tuple[InfoResponse, bool]:
    """
    Sequential aggregator across chunks (and can be continued across documents):
    - If initial_aggregated is provided (InfoResponse or dict), it is used as the starting state.
    - Each chunk updates the aggregation and passes it forward as JSON.
    """
    # Normalize text (document is already pre-chunked in DB)
    text = normalize_to_text(schema)
    logger.info("Extracting info metadata from pre-chunked document")

    # Progress: start processing
    await update_job_progress(
        job_id,
        stage=JobStage.processing_chunks,
        message="Processing document and extracting info metadata",
    )

    parser: PydanticOutputParser[InfoResponse] = PydanticOutputParser(pydantic_object=InfoResponse)
    llm = get_default_llm()
    prompt = ChatPromptTemplate.from_messages(
        [("system", get_info_system_prompt + "\n\n{format_instructions}"), ("human", get_info_user_prompt)]
    ).partial(format_instructions=parser.get_format_instructions())
    chain = make_basic_chain(prompt, llm, parser)

    # Initialize aggregation with previous state if provided
    if initial_aggregated is not None:
        try:
            if isinstance(initial_aggregated, InfoResponse):
                aggregated: InfoResponse = initial_aggregated
            else:
                aggregated = InfoResponse.model_validate(initial_aggregated)
        except Exception:
            aggregated = InfoResponse()
    else:
        aggregated = InfoResponse()

    aggregated_json = aggregated.model_dump_json()

    # Extract summary and tags from doc metadata
    summary, tags = extract_summary_and_tags(doc_metadata)

    # Process the single pre-chunked document
    try:
        logger.info("[Digester:InfoMetadata] Calling LLM for document")
        result: Any = await chain.ainvoke(
            {"chunk": text, "aggregated_json": aggregated_json, "summary": summary, "tags": tags},
            config=RunnableConfig(callbacks=[langfuse_handler]),
        )
        logger.debug("[Digester:InfoMetadata] LLM raw: %r", (result or ""))

        if not result:
            logger.warning("[Digester:InfoMetadata] Empty LLM response")
            error_msg = "[Digester:InfoMetadata] Empty LLM response."
            if doc_id:
                error_msg = f"{error_msg} (Doc: {doc_id})"
            append_job_error(job_id, error_msg)
            return aggregated, False

        # Normalize to InfoResponse
        next_aggregated = result if isinstance(result, InfoResponse) else InfoResponse.model_validate(result)

        # Keep accumulated base endpoints across documents even if a later chunk returns only a subset.
        merged_base_endpoints = InfoMetadata(
            base_api_endpoint=[
                *aggregated.info_about_schema.base_api_endpoint,
                *next_aggregated.info_about_schema.base_api_endpoint,
            ]
        ).base_api_endpoint
        next_aggregated.info_about_schema.base_api_endpoint = merged_base_endpoints

        aggregated = next_aggregated

        logger.info("[Digester:InfoMetadata] Extraction complete for document")
        return aggregated, True

    except Exception as e:
        logger.error("[Digester:InfoMetadata] Document processing failed. Error: %s", e)
        error_msg = f"[Digester:InfoMetadata] Document call failed: {e}"
        if doc_id:
            error_msg = f"{error_msg} (Doc: {doc_id})"
        append_job_error(job_id, error_msg)
        return aggregated, False
