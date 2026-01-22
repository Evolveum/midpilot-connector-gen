#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

import logging
from typing import Any, List, Tuple
from uuid import UUID

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables.config import RunnableConfig

from src.common.chunks import normalize_to_text, split_text_with_token_overlap
from src.common.enums import JobStage
from src.common.jobs import append_job_error, update_job_progress
from src.common.langfuse import langfuse_handler
from src.common.llm import get_default_llm, make_basic_chain

from ..prompts.infoAboutSchemaPrompts import get_info_system_prompt, get_info_user_prompt
from ..schema import InfoResponse
from ..utils.metadata_helper import extract_summary_and_tags

logger = logging.getLogger(__name__)


async def extract_info_metadata(
    schema: str,
    job_id: UUID,
    doc_id: UUID,
    initial_aggregated: Any | None = None,
    doc_metadata: dict[str, Any] | None = None,
) -> Tuple[InfoResponse, List[int]]:
    """
    Sequential aggregator across chunks (and can be continued across documents):
    - If initial_aggregated is provided (InfoResponse or dict), it is used as the starting state.
    - Each chunk updates the aggregation and passes it forward as JSON.
    """
    text = normalize_to_text(schema)
    chunks: List[tuple[str, int]] = split_text_with_token_overlap(text)
    total_chunks = len(chunks)
    logger.info("Extracting info metadata from documentations. Total chunks: %s", total_chunks)

    # Progress: chunking done, start processing
    update_job_progress(
        job_id,
        stage=JobStage.processing_chunks,
        message="Processing chunks and try to extract relevant information",
    )

    parser: PydanticOutputParser[InfoResponse] = PydanticOutputParser(pydantic_object=InfoResponse)
    llm = get_default_llm()
    prompt = ChatPromptTemplate.from_messages(
        [("system", get_info_system_prompt + "\n\n{format_instructions}"), ("human", get_info_user_prompt)]
    ).partial(total=total_chunks, format_instructions=parser.get_format_instructions())
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
    relevant_indices: List[int] = []

    # Extract summary and tags from doc metadata
    summary, tags = extract_summary_and_tags(doc_metadata)

    for idx, chunk in enumerate(chunks, start=1):
        try:
            logger.info("[Digester:InfoMetadata] Calling LLM. Chunk idx: %s", idx)
            result: Any = await chain.ainvoke(
                {"chunk": chunk[0], "aggregated_json": aggregated_json, "summary": summary, "tags": tags},
                config=RunnableConfig(callbacks=[langfuse_handler]),
            )
            logger.debug("[Digester:InfoMetadata] LLM raw: %r", (result or ""))

            if not result:
                logger.warning("[Digester:InfoMetadata] Empty LLM response for chunk %s", idx)
                error_msg = f"[Digester:InfoMetadata] Empty LLM response. Chunk {idx}/{total_chunks}"
                if doc_id:
                    error_msg = f"{error_msg} (Doc: {doc_id})"
                append_job_error(job_id, error_msg)
                continue

            # Normalize to InfoResponse
            aggregated = result if isinstance(result, InfoResponse) else InfoResponse.model_validate(result)

            # If it parsed, mark this zero-based index as relevant
            relevant_indices.append(idx - 1)

            # Pass forward as JSON
            aggregated_json = aggregated.model_dump_json()

        except Exception as e:
            logger.error("[Digester:InfoMetadata] Chunk processing failed. Chunk_idx: %s, error: %s", idx, e)
            error_msg = f"[Digester:InfoMetadata] Chunk {idx}/{total_chunks} call failed: {e}"
            if doc_id:
                error_msg = f"{error_msg} (Doc: {doc_id})"
            append_job_error(job_id, error_msg)

    logger.info("[Digester:InfoMetadata] Chunk extraction complete for document")

    return aggregated, sorted(relevant_indices)
