# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
import re
from typing import Any, Dict, List, Tuple, cast
from uuid import UUID

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables.config import RunnableConfig

from ....common.chunks import get_neighboring_tokens
from ....common.enums import JobStage
from ....common.jobs import (
    append_job_error,
    update_job_progress,
)
from ....common.langfuse import langfuse_handler
from ....common.llm import get_default_llm, make_basic_chain
from ..prompts.endpoints_prompts import (
    check_endpoint_params_system_prompt,
    check_endpoint_params_user_prompt,
    get_endpoints_system_prompt,
    get_endpoints_user_prompt,
)
from ..schema import EndpointInfo, EndpointParamInfo, EndpointsResponse
from ..utils.merges import merge_endpoint_candidates
from ..utils.metadata_helper import extract_summary_and_tags
from ..utils.parallel_docs import process_grouped_chunks_in_parallel

logger = logging.getLogger(__name__)


async def extract_endpoints(
    chunks: List[str],
    object_class: str,
    job_id: UUID,
    base_api_url: str = "",
    chunk_details: List[str] | None = None,
    doc_metadata_map: Dict[str, Dict[str, Any]] | None = None,
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
        chunk_details: Optional list of document UUIDs for each chunk (default: None)
        doc_metadata_map: Optional metadata mapping for documents (default: None)

    Returns:
        Dict containing:
        - "result": Dict with "endpoints" key containing extracted endpoint information
        - "relevantChunks": List of chunks that contained relevant endpoint information
    """

    if chunk_details is None:
        chunk_details = [""] * len(chunks)

    total_chunks = len(chunks)
    logger.info(
        "[Digester:Endpoints] Processing %d pre-selected chunks for %s (docs UUIDs: %s)",
        len(chunks),
        object_class,
        chunk_details,
    )

    # Group chunks by document
    doc_to_chunks: Dict[str, List[str]] = {}
    for chunk_text, doc_uuid in zip(chunks, chunk_details, strict=False):
        doc_to_chunks.setdefault(doc_uuid, []).append(chunk_text)

    total_documents = len(doc_to_chunks)
    logger.info(
        "[Digester:Endpoints] Processing chunks from %d documents for %s",
        total_documents,
        object_class,
    )

    # Initialize document-level progress tracking
    await update_job_progress(
        job_id,
        total_processing=total_documents,
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

    parser: PydanticOutputParser[EndpointsResponse] = PydanticOutputParser(pydantic_object=EndpointsResponse)
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

    async def _extract_for_doc(
        doc_uuid: UUID, doc_chunks: List[str]
    ) -> Tuple[List[EndpointInfo], List[Dict[str, Any]]]:
        """Extract endpoints from chunks of a single document."""
        await update_job_progress(
            job_id,
            stage=JobStage.processing_chunks,
            message="Processing chunks and try to extract relevant information",
        )

        # Get metadata for this document
        doc_metadata = None
        if doc_metadata_map:
            doc_metadata = doc_metadata_map.get(str(doc_uuid))

        doc_relevant_chunks: List[Dict[str, Any]] = []

        async def _process_chunk(chunk_idx: int, chunk: str) -> List[EndpointInfo]:
            one_based = chunk_idx + 1
            try:
                logger.info("[Digester:Endpoints] LLM call for document %s", doc_uuid)

                # Extract summary and tags from doc metadata
                summary, tags = extract_summary_and_tags(doc_metadata)

                result = cast(
                    EndpointsResponse,
                    await chain.ainvoke(
                        {"chunk": chunk, "summary": summary, "tags": tags},  # , "summary": summary, "tags": tags
                        config=RunnableConfig(callbacks=[langfuse_handler]),
                    ),
                )

                if not result or not result.endpoints:
                    return []

                valid_endpoints: List[EndpointInfo] = []

                # Mark this document as relevant if we got endpoints
                if result.endpoints and doc_uuid:
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
                    # Only add once per document
                    if not doc_relevant_chunks or doc_relevant_chunks[0]["docUuid"] != str(doc_uuid):
                        doc_relevant_chunks.append({"docUuid": str(doc_uuid)})

                logger.info(
                    "[Digester:Endpoints] got endpoint %s (doc_uuid: %s)",
                    [ep.path for ep in valid_endpoints],
                    doc_uuid,
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
                                "endpoint": endpoint.model_dump(by_alias=True),
                                "chunk": context_snippet,
                            },
                            config=RunnableConfig(callbacks=[langfuse_handler]),
                        ),
                    )
                    if checked_result:
                        for field_name, value in checked_result.model_dump().items():
                            setattr(endpoint, field_name, value)

                return valid_endpoints

            except Exception as e:
                logger.error("[Digester:Endpoints] Document %s call failed: %s", doc_uuid, e)
                append_job_error(job_id, f"[Digester:Endpoints] Document {doc_uuid} call failed: {e}")
                return []

        # Process all chunks in this document in parallel
        tasks = [_process_chunk(i, chunk_text) for i, chunk_text in enumerate(doc_chunks)]
        results = await asyncio.gather(*tasks)

        # Collect results from this document
        doc_endpoints: List[EndpointInfo] = []
        for endpoints_list in results:
            doc_endpoints.extend(endpoints_list)

        logger.info("[Digester:Endpoints] Extraction completed for document %s", doc_uuid)
        return doc_endpoints, doc_relevant_chunks

    # Process all documents in parallel using the generic function
    results = await process_grouped_chunks_in_parallel(
        doc_to_chunks=doc_to_chunks,
        job_id=job_id,
        extractor=_extract_for_doc,
        logger_scope="Digester:Endpoints",
        total_documents=total_documents,
    )

    # Collect results from all documents
    for doc_endpoints, doc_relevant_chunks in results:
        extracted_endpoints.extend(doc_endpoints)
        relevant_chunk_info.extend(doc_relevant_chunks)

    # Deduplicate and merge
    await update_job_progress(
        job_id,
        stage="merging",
        message=f"Merging, deduplicating and sorting endpoints for {object_class}",
    )

    merged_dicts: List[Dict[str, Any]] = await merge_endpoint_candidates(extracted_endpoints, object_class, job_id)

    logger.info("[Digester:Endpoints] Extraction complete. Unique endpoints: %d", len(merged_dicts))

    await update_job_progress(job_id, stage=JobStage.schema_ready, message="Endpoint extraction complete")

    return {"result": {"endpoints": merged_dicts}, "relevantChunks": relevant_chunk_info}
