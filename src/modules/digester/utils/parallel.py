# Copyright (c) 2025 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import json
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar, cast
from uuid import UUID

from langchain_core.output_parsers import BaseOutputParser, PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables.config import RunnableConfig
from pydantic import BaseModel, ValidationError

from ....common.chunks import normalize_to_text, split_text_with_token_overlap
from ....common.enums import JobStage
from ....common.jobs import append_job_error, update_job_progress
from ....common.langfuse import langfuse_handler
from ....common.llm import get_default_llm, make_basic_chain
from .metadata_helper import extract_summary_and_tags

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


async def run_extraction_parallel(
    schema: str,
    pydantic_model: type[T],
    system_prompt: str,
    user_prompt: str,
    parse_fn: Callable[[T], List[Any]],
    job_id: UUID,
    logger_prefix: str = "",
    doc_id: Optional[UUID] = None,
    track_chunk_per_item: bool = False,
    chunk_metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Any], List[int]]:
    """
    Split schema into chunks and run an LLM extraction concurrently.

    Args:
        schema: The input schema text to be processed
        pydantic_model: The Pydantic model to validate the extracted data against
        system_prompt: The system prompt to use for the LLM
        user_prompt: The user prompt template to use for the LLM
        parse_fn: Function to parse the Pydantic model into a list of items
        job_id: ID of the job for progress tracking
        logger_prefix: Optional prefix for log messages
        doc_id: Optional document ID for tracking
        track_chunk_per_item: If True, returns items with chunk_index attribute set
        chunk_metadata: Optional metadata about the chunk (summary, llm_tags, etc.)

    Returns:
        - Flat list of extracted items (with chunk_index attribute if track_chunk_per_item=True)
        - List of chunk indices that contained relevant data
    """
    text = normalize_to_text(schema)
    chunks: List[tuple[str, int]] = split_text_with_token_overlap(text)
    total_chunks = len(chunks)
    logger.info("%sExtracting. Total chunks: %s", logger_prefix, total_chunks)
    # Progress: chunking done, start processing
    update_job_progress(
        job_id,
        stage=JobStage.processing_chunks,
        current_doc_processed_chunks=0,
        current_doc_total_chunks=total_chunks,
        message="Processing chunks for document",
    )

    parser: BaseOutputParser = PydanticOutputParser(pydantic_object=pydantic_model)
    llm = get_default_llm()
    prompt = ChatPromptTemplate.from_messages(
        [("system", system_prompt + "\n\n{format_instructions}"), ("human", user_prompt)]
    ).partial(format_instructions=parser.get_format_instructions())
    chain = make_basic_chain(prompt, llm, parser)

    # Track which chunks contain relevant data
    relevant_chunk_indices: List[int] = []

    def _result_snippet(result: Any, limit: int = 2000) -> str:
        """Best-effort stringify of a pydantic model or arbitrary object for logging/errors."""
        try:
            if hasattr(result, "model_dump_json"):
                raw = result.model_dump_json()  # pydantic v2
            elif hasattr(result, "model_dump"):
                raw = json.dumps(result.model_dump(by_alias=True))
            else:
                raw = getattr(result, "content", None) or repr(result)
        except Exception:
            raw = repr(result)
        raw = raw if isinstance(raw, str) else str(raw)
        return raw if len(raw) <= limit else raw[:limit] + "...(truncated)"

    async def _process_chunk(idx: int, chunk: str) -> List[Any]:
        one_based = idx + 1
        try:
            logger.info("%sCalling LLM. Chunk idx: %s", logger_prefix, one_based)

            # Extract summary and tags from chunk metadata if available
            summary, tags = extract_summary_and_tags(chunk_metadata)

            result = cast(
                T,
                await chain.ainvoke(
                    {"chunk": chunk, "summary": summary, "tags": tags},
                    config=RunnableConfig(callbacks=[langfuse_handler]),
                ),
            )
            logger.debug("%sLLM result: %r", logger_prefix, (result or ""))

            if not result:
                logger.warning("%sEmpty LLM response. Chunk idx: %s", logger_prefix, one_based)
                error_msg = f"{logger_prefix}Empty LLM response. Chunk {one_based}/{total_chunks}"
                if doc_id:
                    error_msg = f"{error_msg} (Doc: {doc_id})"
                append_job_error(job_id, error_msg)
                return []

            # Parse structured output
            try:
                items = parse_fn(result)
            except (ValidationError, ValueError, json.JSONDecodeError) as e:
                logger.info("%sJSON parse failed; chunk %s. Error: %s", logger_prefix, one_based, e)
                snippet = _result_snippet(result)
                error_msg = (
                    f"{logger_prefix}Parse failed for chunk {one_based}/{total_chunks}: {e}. LLM output: {snippet}"
                )
                if doc_id:
                    error_msg = f"{error_msg} (Doc: {doc_id})"
                append_job_error(job_id, error_msg)
                return []

            # Mark chunk as relevant if we got any items
            if items:
                relevant_chunk_indices.append(idx)
                # If tracking chunk per item, annotate each item with its source chunk
                if track_chunk_per_item:
                    for item in items:
                        if hasattr(item, "__dict__"):
                            item._chunk_index = idx
            return items

        except Exception as e:
            logger.error("%sChunk processing failed. Chunk_idx: %s, error: %s", logger_prefix, one_based, e)
            error_msg = f"{logger_prefix}Chunk {one_based}/{total_chunks} call failed: {e}"
            if doc_id:
                error_msg = f"{error_msg} (Doc: {doc_id})"
            append_job_error(job_id, error_msg)
            return []
        finally:
            update_job_progress(job_id, stage="processing_chunks")

    # Run all chunks
    results = await asyncio.gather(*(_process_chunk(i, ch[0]) for i, ch in enumerate(chunks)))
    all_items = [item for sub in results for item in sub]

    logger.info(
        "%sExtraction complete. Total items: %d, Relevant chunks: %d/%d",
        logger_prefix,
        len(all_items),
        len(relevant_chunk_indices),
        total_chunks,
    )

    return all_items, sorted(relevant_chunk_indices)
