# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar, cast
from uuid import UUID

from langchain_core.output_parsers import BaseOutputParser, PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables.config import RunnableConfig
from pydantic import BaseModel, ValidationError

from src.common.chunks import normalize_to_text
from src.common.enums import JobStage
from src.common.jobs import append_job_error, update_job_progress
from src.common.langfuse import langfuse_handler
from src.common.llm import get_default_llm, make_basic_chain
from src.modules.digester.utils.concurrent_chunk_runner import run_chunks_concurrently
from src.modules.digester.utils.doc_chunk import build_chunk_id_to_doc_id
from src.modules.digester.utils.metadata_helper import extract_summary_and_tags

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


async def run_doc_extractors_concurrently(
    *,
    chunk_items: List[dict],
    job_id: UUID,
    extractor: Callable[[str, UUID, UUID], Any],
    logger_scope: str,
):
    """Run a digester extractor over stored documentation chunks."""
    return await run_chunks_concurrently(
        chunk_items=chunk_items,
        job_id=job_id,
        extractor=extractor,
        logger_scope=logger_scope,
    )


async def process_over_chunks(
    *,
    chunk_items: List[dict],
    job_id: UUID,
    extractor: Callable[[str, UUID, UUID], Any],
    merger: Callable[[List[Dict[str, Any]]], Dict[str, Any]],
    logger_scope: str,
    per_chunk_count: Callable[[Dict[str, Any]], int] | None = None,
) -> Dict[str, Any]:
    """
    Process chunks in parallel, collect relevant chunk references, merge results, and return a digester payload.
    """
    all_results: List[Dict[str, Any]] = []
    all_relevant_chunks: List[Dict[str, Any]] = []
    chunk_id_to_doc_id = build_chunk_id_to_doc_id(chunk_items)

    results = await run_doc_extractors_concurrently(
        chunk_items=chunk_items,
        job_id=job_id,
        extractor=extractor,
        logger_scope=logger_scope,
    )

    for raw_result, has_relevant_data, chunk_id in results:
        if hasattr(raw_result, "model_dump"):
            result_data = cast(Dict[str, Any], raw_result.model_dump(by_alias=True))
        else:
            result_data = cast(Dict[str, Any], raw_result or {})

        if per_chunk_count is not None:
            try:
                count = per_chunk_count(result_data)
            except Exception:
                count = 0
            logger.info("[%s] Chunk %s: extracted %s items", logger_scope, chunk_id, count)

        if result_data:
            all_results.append(result_data)
        if has_relevant_data:
            chunk_id_str = str(chunk_id)
            doc_id = chunk_id_to_doc_id.get(chunk_id_str)
            if doc_id:
                all_relevant_chunks.append({"doc_id": doc_id, "chunk_id": chunk_id_str})
            else:
                logger.warning(
                    "[%s] Missing docId for chunk %s, skipping relevant chunk mapping",
                    logger_scope,
                    chunk_id_str,
                )

    merged_result: Dict[str, Any] = merger(all_results)

    return {
        "result": merged_result,
        "relevantDocumentations": all_relevant_chunks,
    }


def build_chunk_extraction_chain(
    *,
    pydantic_model: type[T],
    system_prompt: str,
    user_prompt: str,
) -> Any:
    """Build a reusable structured extraction chain for repeated chunk invocations."""
    parser: BaseOutputParser = PydanticOutputParser(pydantic_object=pydantic_model)
    llm = get_default_llm()
    prompt = ChatPromptTemplate.from_messages(
        [("system", system_prompt + "\n\n{format_instructions}"), ("human", user_prompt)]
    ).partial(format_instructions=parser.get_format_instructions())
    return make_basic_chain(prompt, llm, parser)


async def extract_single_chunk(
    schema: str,
    pydantic_model: type[T],
    system_prompt: str,
    user_prompt: str,
    parse_fn: Callable[[T], List[Any]],
    job_id: UUID,
    logger_prefix: str = "",
    chunk_id: Optional[UUID] = None,
    track_chunk_per_item: bool = False,
    chunk_metadata: Optional[Dict[str, Any]] = None,
    extraction_chain: Any | None = None,
) -> Tuple[List[Any], bool]:
    """
    Run LLM extraction on a pre-chunked documentation item.

    Since inputs are already pre-chunked in the DB (max 20000 tokens),
    no further splitting is needed - we process the chunk as-is.

    Args:
        schema: The pre-chunked text to be processed
        pydantic_model: The Pydantic model to validate the extracted data against
        system_prompt: The system prompt to use for the LLM
        user_prompt: The user prompt template to use for the LLM
        parse_fn: Function to parse the Pydantic model into a list of items
        job_id: ID of the job for progress tracking
        logger_prefix: Optional prefix for log messages
        chunk_id: Optional chunk ID for tracking
        track_chunk_per_item: Deprecated (kept for backward compatibility, always sets index to 0)
        chunk_metadata: Optional metadata about the chunk (summary, tags, etc.)
        extraction_chain: Optional pre-built reusable extraction chain. When not provided, one is built from prompts.

    Returns:
        - Flat list of extracted items
        - Boolean indicating if any relevant data was found
    """
    # Normalize text (input is already a single pre-chunked unit)
    text = normalize_to_text(schema)

    # Progress: start processing
    await update_job_progress(
        job_id,
        stage=JobStage.processing_chunks,
        message="Processing chunk and extracting relevant information",
    )

    logger.info("%sLLM call for chunk %s", logger_prefix, chunk_id)
    if extraction_chain is None:
        extraction_chain = build_chunk_extraction_chain(
            pydantic_model=pydantic_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

    def _result_snippet(result: Any, limit: int = 2000) -> str:
        """Best-effort stringify of a pydantic model or arbitrary object for logging/errors."""
        try:
            if hasattr(result, "model_dump_json"):
                raw = result.model_dump_json()  # pydantic v2
            elif hasattr(result, "model_dump"):
                raw = json.dumps(result.model_dump(by_alias=True, mode="json"))
            else:
                raw = getattr(result, "content", None) or repr(result)
        except Exception:
            raw = repr(result)
        raw = raw if isinstance(raw, str) else str(raw)
        return raw if len(raw) <= limit else raw[:limit] + "...(truncated)"

    # Process the chunk (already pre-chunked)
    try:
        # Extract summary and tags from chunk metadata
        summary, tags = extract_summary_and_tags(chunk_metadata)

        result = cast(
            T,
            await extraction_chain.ainvoke(
                {"chunk": text, "summary": summary, "tags": tags},
                config=RunnableConfig(callbacks=[langfuse_handler]),
            ),
        )
        logger.debug("%sLLM result: %r", logger_prefix, (result or ""))

        if not result:
            error_message = f"{logger_prefix}Empty LLM response."
            if chunk_id:
                error_message = f"{error_message} (chunk_id: {chunk_id})"
            logger.warning(error_message)
            append_job_error(job_id, error_message)
            return [], False

        # Parse structured output
        try:
            items = parse_fn(result)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            snippet = _result_snippet(result)
            error_message = f"{logger_prefix}Failed to parse LLM output: {exc}. LLM output: {snippet}"
            if chunk_id:
                error_message = f"{error_message} (chunk_id: {chunk_id})"
            logger.exception(error_message)
            append_job_error(job_id, error_message)
            return [], False

        has_relevant_data = bool(items)

        if track_chunk_per_item and items:
            for item in items:
                if hasattr(item, "__dict__"):
                    item._chunk_index = 0

        return items, has_relevant_data

    except Exception as exc:
        error_message = f"{logger_prefix}Failed to process chunk: {exc}"
        if chunk_id:
            error_message = f"{error_message} (chunk_id: {chunk_id})"
        logger.exception(error_message)
        append_job_error(job_id, error_message)
        return [], False
