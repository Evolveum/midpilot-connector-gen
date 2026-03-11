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

from ....common.chunks import normalize_to_text
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
) -> Tuple[List[Any], bool]:
    """
    Run LLM extraction on a pre-chunked document.

    Since documents are already pre-chunked in the DB (max 20000 tokens),
    no further splitting is needed - we process the document as-is.

    Args:
        schema: The pre-chunked document text to be processed
        pydantic_model: The Pydantic model to validate the extracted data against
        system_prompt: The system prompt to use for the LLM
        user_prompt: The user prompt template to use for the LLM
        parse_fn: Function to parse the Pydantic model into a list of items
        job_id: ID of the job for progress tracking
        logger_prefix: Optional prefix for log messages
        doc_id: Optional document ID for tracking
        track_chunk_per_item: Deprecated (kept for backward compatibility, always sets index to 0)
        chunk_metadata: Optional metadata about the document (summary, tags, etc.)

    Returns:
        - Flat list of extracted items
        - Boolean indicating if any relevant data was found
    """
    # Normalize text (document is already a single pre-chunked unit)
    text = normalize_to_text(schema)

    # Progress: start processing
    await update_job_progress(
        job_id,
        stage=JobStage.processing_chunks,
        message="Processing document and extracting relevant information",
    )

    # Build LLM chain
    parser: BaseOutputParser = PydanticOutputParser(pydantic_object=pydantic_model)
    llm = get_default_llm()
    prompt = ChatPromptTemplate.from_messages(
        [("system", system_prompt + "\n\n{format_instructions}"), ("human", user_prompt)]
    ).partial(format_instructions=parser.get_format_instructions())
    chain = make_basic_chain(prompt, llm, parser)

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

    # Process the document (already pre-chunked)
    try:
        logger.info("%sCalling LLM for document extraction.", logger_prefix)

        # Extract summary and tags from document metadata
        summary, tags = extract_summary_and_tags(chunk_metadata)

        result = cast(
            T,
            await chain.ainvoke(
                {"chunk": text, "summary": summary, "tags": tags},
                config=RunnableConfig(callbacks=[langfuse_handler]),
            ),
        )
        logger.debug("%sLLM result: %r", logger_prefix, (result or ""))

        if not result:
            logger.warning("%sEmpty LLM response.", logger_prefix)
            error_msg = f"{logger_prefix}Empty LLM response."
            if doc_id:
                error_msg = f"{error_msg} (Doc: {doc_id})"
            append_job_error(job_id, error_msg)
            return [], False

        # Parse structured output
        try:
            items = parse_fn(result)
        except (ValidationError, ValueError, json.JSONDecodeError) as e:
            logger.info("%sJSON parse failed. Error: %s", logger_prefix, e)
            snippet = _result_snippet(result)
            error_msg = f"{logger_prefix}Parse failed: {e}. LLM output: {snippet}"
            if doc_id:
                error_msg = f"{error_msg} (Doc: {doc_id})"
            append_job_error(job_id, error_msg)
            return [], False

        has_relevant_data = bool(items)

        # Optional: annotate items with chunk index (for backward compatibility, always 0 now)
        if track_chunk_per_item and items:
            for item in items:
                if hasattr(item, "__dict__"):
                    item._chunk_index = 0

        logger.info(
            "%sExtraction complete. Total items: %d, has_relevant_data=%s",
            logger_prefix,
            len(items),
            has_relevant_data,
        )

        return items, has_relevant_data

    except Exception as e:
        logger.error("%sDocument processing failed. Error: %s", logger_prefix, e)
        error_msg = f"{logger_prefix}Document call failed: {e}"
        if doc_id:
            error_msg = f"{error_msg} (Doc: {doc_id})"
        append_job_error(job_id, error_msg)
        return [], False
