# Copyright (c) 2025 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
from typing import Any, Dict, List, Tuple, cast
from uuid import UUID

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables.config import RunnableConfig

from ....common.enums import JobStage
from ....common.jobs import (
    append_job_error,
    update_job_progress,
)
from ....common.langfuse import langfuse_handler
from ....common.llm import get_default_llm, make_basic_chain
from ..prompts.endpointsPrompts import (
    get_endpoints_system_prompt,
    get_endpoints_user_prompt,
)
from ..schema import EndpointInfo, EndpointsResponse
from .parallel_docs import process_grouped_chunks_in_parallel

logger = logging.getLogger(__name__)

_METHOD_ORDER: Dict[str, int] = {"GET": 0, "HEAD": 1, "OPTIONS": 2, "POST": 3, "PUT": 4, "PATCH": 5, "DELETE": 6}


def _normalize_method(method: str) -> str:
    return (method or "").strip().upper()


def _endpoint_key(ep: EndpointInfo) -> Tuple[str, str]:
    return (ep.path.strip(), _normalize_method(ep.method))


async def extract_endpoints(
    chunks: List[str],
    object_class: str,
    job_id: UUID,
    base_api_url: str = "",
    chunk_details: List[Tuple[int, str]] | None = None,
    doc_metadata_map: Dict[str, Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """
    Extract endpoints from pre-selected chunks without re-chunking.
    Processes each chunk directly through the LLM.

    Args:
        chunks: Pre-selected chunk texts
        object_class: Name of the object class
        job_id: Job ID for progress tracking
        base_api_url: Base API URL
        chunk_details: List of (original_chunk_index, doc_uuid) for logging

    Returns:
        Dict with {"result": EndpointsResponse, "relevantChunks": [...]}
    """

    if chunk_details is None:
        chunk_details = [(i, "") for i in range(len(chunks))]

    total_chunks = len(chunks)
    logger.info("[Digester:Endpoints] Processing %d pre-selected chunks", total_chunks)

    # Group chunks by document
    doc_to_chunks: Dict[str, List[Tuple[int, int, str]]] = {}
    for idx, (original_idx, doc_uuid) in enumerate(chunk_details):
        if doc_uuid not in doc_to_chunks:
            doc_to_chunks[doc_uuid] = []
        doc_to_chunks[doc_uuid].append((idx, original_idx, chunks[idx]))

    total_documents = len(doc_to_chunks)
    logger.info(
        "[Digester:Endpoints] Processing chunks from %d documents: %s",
        total_documents,
        {doc_uuid: len(chunks_list) for doc_uuid, chunks_list in doc_to_chunks.items()},
    )

    # Initialize document-level progress tracking
    update_job_progress(
        job_id,
        total_documents=total_documents,
        processed_documents=0,
        message="Processing selected chunks",
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

    # Process each chunk
    extracted_endpoints: List[EndpointInfo] = []
    relevant_chunk_info: List[Dict[str, Any]] = []

    async def _extract_for_doc(
        doc_uuid: UUID, doc_chunks: List[Tuple[int, int, str]], doc_idx: int
    ) -> Tuple[List[EndpointInfo], List[Dict[str, Any]]]:
        """Extract endpoints from chunks of a single document."""
        num_chunks_in_doc = len(doc_chunks)
        update_job_progress(
            job_id,
            stage=JobStage.processing_chunks,
            message=f"Processing {num_chunks_in_doc} chunks from document {doc_idx}/{total_documents} for {object_class}",
        )

        # Disable for now metadata in endpoints extraction
        # Get metadata for this document
        # doc_metadata = None
        # if doc_metadata_map:
        #     doc_metadata = doc_metadata_map.get(str(doc_uuid))

        doc_relevant_chunks: List[Dict[str, Any]] = []

        async def _process_chunk(
            array_idx: int, chunk: str, original_idx: int, doc_uuid: UUID, doc_metadata: Dict[str, Any] | None = None
        ) -> List[EndpointInfo]:
            one_based = array_idx + 1
            try:
                logger.info(
                    "[Digester:Endpoints] LLM call %d/%d (original chunk index: %d, doc_uuid: %s)",
                    one_based,
                    total_chunks,
                    original_idx,
                    doc_uuid,
                )

                # Extract summary and tags from doc metadata
                # summary, tags = extract_summary_and_tags(doc_metadata)

                result = cast(
                    EndpointsResponse,
                    await chain.ainvoke(
                        {"chunk": chunk},  # , "summary": summary, "tags": tags
                        config=RunnableConfig(callbacks=[langfuse_handler]),
                    ),
                )

                if not result or not result.endpoints:
                    return []

                # Mark this chunk as relevant if we got endpoints
                if result.endpoints:
                    doc_relevant_chunks.append({"docUuid": doc_uuid, "chunkIndex": original_idx})

                return result.endpoints

            except Exception as e:
                logger.warning("[Digester:Endpoints] Error processing chunk %d: %s", one_based, str(e))
                append_job_error(job_id, f"[Digester:Endpoints] Error processing chunk {one_based}: {str(e)}")
                return []

        # Process all chunks in this document in parallel
        tasks = [
            _process_chunk(array_idx, chunk_text, original_idx, doc_uuid, None)  # doc_metadata
            for array_idx, original_idx, chunk_text in doc_chunks
        ]
        results = await asyncio.gather(*tasks)

        # Collect results from this document
        doc_endpoints: List[EndpointInfo] = []
        for endpoints_list in results:
            doc_endpoints.extend(endpoints_list)

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
    update_job_progress(
        job_id,
        stage="merging",
        message=f"Merging, deduplicating and sorting endpoints for {object_class}",
    )

    by_key: Dict[Tuple[str, str], EndpointInfo] = {}
    for ep in extracted_endpoints:
        if not ep.path or not ep.method:
            continue

        ep.method = _normalize_method(ep.method)
        key = _endpoint_key(ep)

        if key not in by_key:
            by_key[key] = ep
            continue

        current = by_key[key]
        # Prefer longer, non-empty description
        if (ep.description or "") and len(ep.description) > len(current.description or ""):
            current.description = ep.description

        # Prefer non-empty content types
        if not current.request_content_type and ep.request_content_type:
            current.request_content_type = ep.request_content_type
        if not current.response_content_type and ep.response_content_type:
            current.response_content_type = ep.response_content_type

        # Merge suggested_use (unique, preserve order)
        if ep.suggested_use:
            existing = list(current.suggested_use or [])
            for su in ep.suggested_use:
                if su not in existing:
                    existing.append(su)
            current.suggested_use = existing

    merged = list(by_key.values())

    # Sort by path, then by common HTTP method order
    merged.sort(key=lambda e: (e.path, _METHOD_ORDER.get(_normalize_method(e.method), 99), e.method))

    # Convert EndpointInfo objects to dicts for JSON serialization
    merged_dicts = [ep.model_dump(by_alias=True) if hasattr(ep, "model_dump") else ep for ep in merged]

    logger.info("[Digester:Endpoints] Extraction complete. Unique endpoints: %d", len(merged_dicts))
    update_job_progress(
        job_id,
        stage=JobStage.finished,
        message="complete",
    )

    return {"result": {"endpoints": merged_dicts}, "relevantChunks": relevant_chunk_info}
