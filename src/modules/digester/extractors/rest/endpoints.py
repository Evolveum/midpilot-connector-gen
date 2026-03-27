# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
import re
from typing import Any, Dict, List, Set, Tuple, cast
from uuid import UUID

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables.config import RunnableConfig

from src.common.chunks import get_neighboring_tokens
from src.common.enums import JobStage
from src.common.jobs import (
    append_job_error,
    update_job_progress,
)
from src.common.langfuse import langfuse_handler
from src.common.llm import get_default_llm, make_basic_chain
from src.common.utils.normalize import normalize_chunk_pair, normalize_endpoint_key
from src.modules.digester.prompts.rest.endpoints_prompts import (
    check_endpoint_params_system_prompt,
    check_endpoint_params_user_prompt,
    get_endpoints_system_prompt,
    get_endpoints_user_prompt,
)
from src.modules.digester.schema import EndpointInfo, EndpointParamInfo, EndpointResponse
from src.modules.digester.utils.merges import merge_endpoint_candidates
from src.modules.digester.utils.metadata_helper import extract_summary_and_tags
from src.modules.digester.utils.parallel_docs import process_grouped_chunks_in_parallel

logger = logging.getLogger(__name__)


def _attach_relevant_documentations_per_endpoint(
    endpoints: List[Dict[str, Any]],
    endpoint_chunk_pairs: Dict[Tuple[str, str], Set[Tuple[str, str]]],
) -> List[Dict[str, Any]]:
    """Attach per-endpoint relevantDocumentations in camelCase."""
    enriched: List[Dict[str, Any]] = []

    for endpoint in endpoints:
        endpoint_copy = dict(endpoint)
        key = normalize_endpoint_key(endpoint_copy.get("path"), endpoint_copy.get("method"))
        pairs = sorted(endpoint_chunk_pairs.get(key, set()), key=lambda pair: (pair[0], pair[1])) if key else []
        endpoint_copy["relevantDocumentations"] = [{"docId": doc_id, "chunkId": chunk_id} for doc_id, chunk_id in pairs]
        enriched.append(endpoint_copy)

    return enriched


async def extract_endpoints(
    chunks: List[str],
    object_class: str,
    job_id: UUID,
    base_api_url: str = "",
    chunk_details: List[str] | None = None,
    chunk_metadata_map: Dict[str, Dict[str, Any]] | None = None,
    chunk_id_to_doc_id: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    """
    Extract API endpoints from document chunks using LLM analysis.

    Processes chunks of text to identify and extract API endpoint information including
    paths, methods, parameters, and metadata. Uses parallel processing for efficiency
    and includes parameter validation through context analysis.

    Args:
        chunks: List of text chunks to analyze for endpoint information
        object_class: Target object class for endpoint extraction context
        job_id: UUID for job tracking and progress updates
        base_api_url: Base URL for API endpoints (default: "")
        chunk_details: Optional list of chunk IDs for each chunk (default: None)
        chunk_metadata_map: Optional metadata mapping for chunks (default: None)
        chunk_id_to_doc_id: Optional mapping of chunk ID to doc ID

    Returns:
        Dict containing:
        - "result": Dict with "endpoints" key containing extracted endpoint information
        - "relevantDocumentations": List of chunks that contained relevant endpoint information
    """

    if chunk_details is None:
        chunk_details = [""] * len(chunks)

    total_chunks = len(chunks)
    logger.info(
        "[Digester:Endpoints] Processing %d pre-selected chunks for %s (chunk IDs: %s)",
        len(chunks),
        object_class,
        chunk_details,
    )

    # Group chunks by chunk_id
    chunks_by_id: Dict[str, List[str]] = {}
    for chunk_text, chunk_id in zip(chunks, chunk_details, strict=False):
        chunks_by_id.setdefault(chunk_id, []).append(chunk_text)

    total_chunk_ids = len(chunks_by_id)
    logger.info(
        "[Digester:Endpoints] Processing chunks from %d groups for %s",
        total_chunk_ids,
        object_class,
    )

    # Initialize grouped progress tracking
    await update_job_progress(
        job_id,
        total_processing=total_chunk_ids,
        processing_completed=0,
        message="Processing chunks and try to extract relevant information",
    )

    # Prepare prompts
    system_prompt = get_endpoints_system_prompt.replace("{object_class}", object_class).replace(
        "{base_api_url}", base_api_url
    )
    user_prompt = get_endpoints_user_prompt.replace("{object_class}", object_class).replace(
        "{base_api_url}", base_api_url
    )

    parser: PydanticOutputParser[EndpointResponse] = PydanticOutputParser(pydantic_object=EndpointResponse)
    llm = get_default_llm()
    prompt = ChatPromptTemplate.from_messages(
        [("system", system_prompt + "\n\n{format_instructions}"), ("human", user_prompt)]
    ).partial(total=total_chunks, format_instructions=parser.get_format_instructions())
    chain = make_basic_chain(prompt, llm, parser)

    param_parser: PydanticOutputParser[EndpointParamInfo] = PydanticOutputParser(pydantic_object=EndpointParamInfo)
    param_system_prompt = check_endpoint_params_system_prompt.replace("{object_class}", object_class)
    param_user_prompt = check_endpoint_params_user_prompt.replace("{object_class}", object_class)
    param_prompt = ChatPromptTemplate.from_messages(
        [("system", param_system_prompt + "\n\n{format_instructions}"), ("human", param_user_prompt)]
    ).partial(format_instructions=param_parser.get_format_instructions())
    param_chain = make_basic_chain(param_prompt, llm, param_parser)

    # Process each chunk
    extracted_endpoints: List[EndpointInfo] = []
    relevant_chunk_info: List[Dict[str, Any]] = []
    endpoint_chunk_pairs: Dict[Tuple[str, str], Set[Tuple[str, str]]] = {}

    async def _extract_for_chunk_id(
        chunk_id: UUID, chunks_for_chunk_id: List[str]
    ) -> Tuple[List[EndpointInfo], List[Dict[str, Any]]]:
        """Extract endpoints from chunks associated with one chunk_id."""
        await update_job_progress(
            job_id,
            stage=JobStage.processing_chunks,
            message="Processing chunks and try to extract relevant information",
        )

        # Get metadata for this chunk_id
        chunk_metadata = None
        if chunk_metadata_map:
            chunk_metadata = chunk_metadata_map.get(str(chunk_id))

        relevant_chunks_for_id: List[Dict[str, Any]] = []

        async def _process_chunk(chunk_idx: int, chunk: str) -> List[EndpointInfo]:
            one_based = chunk_idx + 1
            try:
                logger.info("[Digester:Endpoints] LLM call for chunk %s", chunk_id)

                # Extract summary and tags from chunk metadata
                summary, tags = extract_summary_and_tags(chunk_metadata)

                result = cast(
                    EndpointResponse,
                    await chain.ainvoke(
                        {"chunk": chunk, "summary": summary, "tags": tags},
                        config=RunnableConfig(callbacks=[langfuse_handler]),
                    ),
                )

                if not result or not result.endpoints:
                    return []

                valid_endpoints: List[EndpointInfo] = []

                # Mark this chunk_id as relevant if we got endpoints
                if result.endpoints and chunk_id:
                    for endpoint in result.endpoints:
                        if endpoint.path:
                            if re.search(re.escape(endpoint.path) + r'[\s\n\t.,;:!?\-\)\]\}"\']', chunk, re.IGNORECASE):
                                valid_endpoints.append(endpoint)
                            else:
                                logger.info(
                                    "[Digester:Endpoints] Extracted path '%s' not found in chunk %d, deleting path",
                                    endpoint.path,
                                    one_based,
                                )

                if valid_endpoints:
                    # Only add once per chunk_id
                    if not relevant_chunks_for_id:
                        chunk_id_str = str(chunk_id)
                        doc_id = chunk_id_to_doc_id.get(chunk_id_str) if chunk_id_to_doc_id else None
                        if doc_id:
                            relevant_chunks_for_id.append({"doc_id": doc_id, "chunk_id": chunk_id_str})
                        else:
                            logger.warning(
                                "[Digester:Endpoints] Missing docId for chunk %s, skipping relevant chunk mapping",
                                chunk_id_str,
                            )

                logger.info(
                    "[Digester:Endpoints] got endpoint %s (chunk_id: %s)",
                    [ep.path for ep in valid_endpoints],
                    chunk_id,
                )

                # In this step, we are validating parameters of the extracted endpoints
                # we choose 1000 tokens around the found endpoint in text and run the llm on it
                for endpoint in valid_endpoints:
                    context_snippet = get_neighboring_tokens(
                        search_phrase=endpoint.path or "",
                        text=chunk,
                        context_token_count_before=150,
                        context_token_count_after=1000,
                    )
                    checked_result = cast(
                        EndpointParamInfo,
                        await param_chain.ainvoke(
                            {
                                "endpoint": endpoint.model_dump(by_alias=True, exclude={"relevant_documentations"}),
                                "chunk": context_snippet,
                            },
                            config=RunnableConfig(callbacks=[langfuse_handler]),
                        ),
                    )
                    if checked_result:
                        for field_name, value in checked_result.model_dump().items():
                            setattr(endpoint, field_name, value)

                return valid_endpoints

            except Exception as exc:
                error_message = f"[Digester:Endpoints] Failed to process chunk {chunk_id}: {exc}"
                logger.exception(error_message)
                append_job_error(job_id, error_message)
                return []

        tasks = [_process_chunk(i, chunk_text) for i, chunk_text in enumerate(chunks_for_chunk_id)]
        results = await asyncio.gather(*tasks)

        # Collect results for this chunk_id
        endpoints_for_id: List[EndpointInfo] = []
        for endpoints_list in results:
            endpoints_for_id.extend(endpoints_list)

        logger.info("[Digester:Endpoints] Extraction completed for chunk %s", chunk_id)
        return endpoints_for_id, relevant_chunks_for_id

    # Process all chunk-id groups in parallel using the generic function
    results = await process_grouped_chunks_in_parallel(
        chunks_by_id=chunks_by_id,
        job_id=job_id,
        extractor=_extract_for_chunk_id,
        logger_scope="Digester:Endpoints",
        total_groups=total_chunk_ids,
    )

    # Collect results from all groups
    for endpoints_for_id, relevant_chunks_for_id in results:
        extracted_endpoints.extend(endpoints_for_id)
        relevant_chunk_info.extend(relevant_chunks_for_id)

        normalized_pairs = [normalize_chunk_pair(chunk_ref) for chunk_ref in relevant_chunks_for_id]
        valid_pairs = [pair for pair in normalized_pairs if pair is not None]
        if not valid_pairs:
            continue

        for endpoint in endpoints_for_id:
            key = normalize_endpoint_key(endpoint.path, endpoint.method)
            if not key:
                continue
            seen_pairs = endpoint_chunk_pairs.setdefault(key, set())
            for doc_id, chunk_id in valid_pairs:
                seen_pairs.add((doc_id, chunk_id))

    # Deduplicate and merge
    await update_job_progress(
        job_id,
        stage="merging",
        message=f"Merging, deduplicating and sorting endpoints for {object_class}",
    )

    merged_dicts: List[Dict[str, Any]] = await merge_endpoint_candidates(extracted_endpoints, object_class, job_id)
    merged_with_references = _attach_relevant_documentations_per_endpoint(merged_dicts, endpoint_chunk_pairs)

    logger.info("[Digester:Endpoints] Extraction complete. Unique endpoints: %d", len(merged_with_references))

    await update_job_progress(job_id, stage=JobStage.schema_ready, message="Endpoint extraction complete")

    return {"result": {"endpoints": merged_with_references}, "relevantDocumentations": relevant_chunk_info}
