# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from src.jobs import update_job_progress
from src.modules.digester.aggregation.merges import merge_api_type, merge_info_metadata
from src.modules.digester.extraction.chunk_extraction import extract_single_chunk, run_doc_extractors_concurrently
from src.modules.digester.extraction.metadata_helper import build_doc_metadata_map
from src.modules.digester.extractors.apitype.availability import (
    summarize_rest_availability,
    summarize_scim_availability,
)
from src.modules.digester.extractors.apitype.documentation import extract_api_type as _extract_api_type
from src.modules.digester.extractors.apitype.knowledge import lookup_api_type_knowledge, lookup_rest_knowledge
from src.modules.digester.extractors.apitype.scim_cloud import lookup_scim_support
from src.modules.digester.extractors.apitype.web_search import lookup_api_type_web_search, lookup_rest_web_search
from src.modules.digester.prompts.info_prompts import get_info_system_prompt, get_info_user_prompt
from src.modules.digester.schemas import (
    ApiTypeResponse,
    InfoExtractionResponse,
    InfoMetadataExtraction,
    RestAvailabilityInfo,
    ScimAvailabilityInfo,
)
from src.modules.digester.selection import build_chunk_id_to_doc_id, resolve_relevant_chunk_ref
from src.shared.coerce import as_str
from src.shared.enums import ApiType, DetectionSource

logger = logging.getLogger(__name__)


async def extract_info_metadata_chunk(
    schema: str,
    job_id: UUID,
    chunk_id: Optional[UUID] = None,
    chunk_metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[List[InfoMetadataExtraction], bool]:
    """
    Extract raw info metadata from a single chunk with a standalone LLM call.
    Does NOT extract apiType (handled by the dedicated apiType extractor) and does NOT
    aggregate across chunks - aggregation is handled in the service layer.

    Returns:
        - List of extracted InfoMetadataExtraction candidates (0 or 1 item)
        - Boolean indicating if relevant data was found
    """

    def parse_fn(result: InfoExtractionResponse) -> List[InfoMetadataExtraction]:
        info = result.info_metadata
        if info is None or info.is_empty():
            return []
        return [info]

    extracted, has_relevant_data = await extract_single_chunk(
        schema=schema,
        pydantic_model=InfoExtractionResponse,
        system_prompt=get_info_system_prompt,
        user_prompt=get_info_user_prompt,
        parse_fn=parse_fn,
        logger_prefix="[Digester:InfoMetadata] ",
        job_id=job_id,
        chunk_id=chunk_id,
        chunk_metadata=chunk_metadata,
    )

    logger.info(
        "[Digester:InfoMetadata] Extraction complete. has_relevant_data=%s",
        has_relevant_data,
    )
    return extracted, has_relevant_data


async def extract_info_metadata(doc_items: List[dict], application_name: str, job_id: UUID):
    """
    Extract metadata from multiple documentation items in parallel.

    Step 1: Run two independent per-chunk extractors concurrently:
            - info metadata (name, versions, base endpoints, database name),
            - apiType (REST/SCIM/SQL protocol detection) as a standalone LLM call,
            plus three documentation-free SCIM signals for the discovery application name:
            a scim.cloud registry lookup, an LLM knowledge lookup, and a web-search lookup.
    Step 2: Merge both sets of candidates using threshold-based heuristics, union in SCIM
            when any documentation-free signal confirms it, and join the detected apiType
            into one final InfoResponse payload. SCIM availability (paid vs generally
            available) is aggregated from the signals and logged only (not in the response).
    """
    all_info_candidates: List[InfoMetadataExtraction] = []
    all_api_type_candidates: List[ApiTypeResponse] = []
    relevant_chunks_by_id: Dict[str, Dict[str, str]] = {}
    chunk_id_to_doc_id = build_chunk_id_to_doc_id(doc_items)
    chunk_metadata_map = build_doc_metadata_map(doc_items)

    async def info_extractor_with_metadata(content: str, job_id: UUID, chunk_id: UUID):
        chunk_metadata = chunk_metadata_map.get(str(chunk_id))
        return await extract_info_metadata_chunk(content, job_id, chunk_id, chunk_metadata)

    async def api_type_extractor_with_metadata(content: str, job_id: UUID, chunk_id: UUID):
        chunk_metadata = chunk_metadata_map.get(str(chunk_id))
        return await _extract_api_type(content, job_id, chunk_id, chunk_metadata)

    def track_relevant_chunk(chunk_id: UUID) -> None:
        chunk_id_str = str(chunk_id)
        if chunk_id_str in relevant_chunks_by_id:
            return
        chunk_ref = resolve_relevant_chunk_ref(chunk_id, chunk_id_to_doc_id, "Digester:InfoMetadata")
        if chunk_ref is not None:
            relevant_chunks_by_id[chunk_id_str] = chunk_ref

    await update_job_progress(
        job_id,
        total_processing=len(doc_items) * 2,
        message="Processing chunks",
    )

    application_name = as_str(application_name).strip()

    (
        info_results,
        api_type_results,
        scim_cloud_match,
        knowledge_result,
        web_search_result,
        rest_knowledge_result,
        rest_web_search_result,
    ) = await asyncio.gather(
        run_doc_extractors_concurrently(
            chunk_items=doc_items,
            job_id=job_id,
            extractor=info_extractor_with_metadata,
            set_total=False,
        ),
        run_doc_extractors_concurrently(
            chunk_items=doc_items,
            job_id=job_id,
            extractor=api_type_extractor_with_metadata,
            set_total=False,
        ),
        lookup_scim_support(application_name),
        lookup_api_type_knowledge(application_name),
        lookup_api_type_web_search(application_name),
        lookup_rest_knowledge(application_name),
        lookup_rest_web_search(application_name),
    )

    for raw_infos, has_relevant_data, chunk_id in info_results:
        normalized_infos: List[InfoMetadataExtraction] = []

        if isinstance(raw_infos, list):
            for item in raw_infos:
                if isinstance(item, InfoMetadataExtraction):
                    normalized_infos.append(item)
                    continue
                if isinstance(item, InfoExtractionResponse):
                    if item.info_metadata is not None:
                        normalized_infos.append(item.info_metadata)
                    continue
                if isinstance(item, dict):
                    try:
                        parsed_info = InfoExtractionResponse.model_validate(item).info_metadata
                        if parsed_info is not None:
                            normalized_infos.append(parsed_info)
                        continue
                    except Exception as response_exc:
                        try:
                            normalized_infos.append(InfoMetadataExtraction.model_validate(item))
                        except Exception as metadata_exc:
                            logger.warning(
                                "[Digester:InfoMetadata] Dropping invalid metadata item from chunk %s after "
                                "InfoExtractionResponse and InfoMetadataExtraction validation failed. errors=%s/%s",
                                chunk_id,
                                type(response_exc).__name__,
                                type(metadata_exc).__name__,
                            )
                            continue
        elif isinstance(raw_infos, InfoMetadataExtraction):
            normalized_infos.append(raw_infos)
        elif isinstance(raw_infos, InfoExtractionResponse):
            if raw_infos.info_metadata is not None:
                normalized_infos.append(raw_infos.info_metadata)
        elif isinstance(raw_infos, dict):
            try:
                parsed_info = InfoExtractionResponse.model_validate(raw_infos).info_metadata
                if parsed_info is not None:
                    normalized_infos.append(parsed_info)
            except Exception as response_exc:
                try:
                    normalized_infos.append(InfoMetadataExtraction.model_validate(raw_infos))
                except Exception as metadata_exc:
                    logger.warning(
                        "[Digester:InfoMetadata] Dropping invalid metadata payload from chunk %s after "
                        "InfoExtractionResponse and InfoMetadataExtraction validation failed. errors=%s/%s",
                        chunk_id,
                        type(response_exc).__name__,
                        type(metadata_exc).__name__,
                    )

        logger.info(
            "[Digester:InfoMetadata] Chunk %s: extracted %s metadata candidates",
            chunk_id,
            len(normalized_infos),
        )
        all_info_candidates.extend(normalized_infos)

        if has_relevant_data:
            track_relevant_chunk(chunk_id)

    for raw_api_types, has_relevant_data, chunk_id in api_type_results:
        normalized_api_types: List[ApiTypeResponse] = []

        candidates = raw_api_types if isinstance(raw_api_types, list) else [raw_api_types]
        for item in candidates:
            if isinstance(item, ApiTypeResponse):
                normalized_api_types.append(item)
            elif isinstance(item, dict):
                try:
                    normalized_api_types.append(ApiTypeResponse.model_validate(item))
                except Exception as exc:
                    logger.warning(
                        "[Digester:ApiType] Dropping invalid apiType payload from chunk %s: %s",
                        chunk_id,
                        type(exc).__name__,
                    )

        logger.info(
            "[Digester:ApiType] Chunk %s: extracted %s apiType candidates",
            chunk_id,
            len(normalized_api_types),
        )
        all_api_type_candidates.extend(normalized_api_types)

        if has_relevant_data:
            track_relevant_chunk(chunk_id)

    logger.info(
        "[Digester:InfoMetadata] Processing complete. Total: %s info candidates and %s apiType candidates "
        "from %s chunks. Starting heuristic merge...",
        len(all_info_candidates),
        len(all_api_type_candidates),
        len(doc_items),
    )

    api_types = merge_api_type(all_api_type_candidates, total_items=len(doc_items))
    scim_detected_in_docs = ApiType.SCIM in api_types
    rest_detected_in_docs = ApiType.REST in api_types
    if ApiType.SCIM not in api_types and (
        scim_cloud_match.matched or knowledge_result.supports_scim or web_search_result.supports_scim
    ):
        if scim_cloud_match.matched:
            logger.info(
                "[Digester:ApiType] scim.cloud confirmed SCIM for '%s' (matched '%s'); adding SCIM to detected apiType",
                application_name,
                scim_cloud_match.project_name,
            )
        if knowledge_result.supports_scim:
            logger.info(
                "[Digester:ApiType] LLM knowledge signal reports SCIM support for '%s'; adding SCIM to detected apiType",
                application_name,
            )
        if web_search_result.supports_scim:
            logger.info(
                "[Digester:ApiType] Web search signal reports SCIM support for '%s'; adding SCIM to detected apiType",
                application_name,
            )
        api_types = sorted([*api_types, ApiType.SCIM], key=lambda api_type: api_type.value)

    if ApiType.REST not in api_types and (rest_knowledge_result.supports_rest or rest_web_search_result.supports_rest):
        if rest_knowledge_result.supports_rest:
            logger.info(
                "[Digester:ApiType] LLM knowledge signal reports REST support for '%s'; adding REST to detected apiType",
                application_name,
            )
        if rest_web_search_result.supports_rest:
            logger.info(
                "[Digester:ApiType] Web search signal reports REST support for '%s'; adding REST to detected apiType",
                application_name,
            )
        api_types = sorted([*api_types, ApiType.REST], key=lambda api_type: api_type.value)

    scim_availability_info: ScimAvailabilityInfo | None = None
    if ApiType.SCIM in api_types:
        availability = summarize_scim_availability(
            {
                DetectionSource.KNOWLEDGE: knowledge_result,
                DetectionSource.WEB_SEARCH: web_search_result,
            }
        )
        contributing = set(availability.sources)
        if scim_detected_in_docs:
            contributing.add(DetectionSource.DOCUMENTATION)
        if scim_cloud_match.matched:
            contributing.add(DetectionSource.SCIM_CLOUD)
        # Report sources in a stable order (the enum's declaration order).
        sources = [source for source in DetectionSource if source in contributing]
        scim_availability_info = ScimAvailabilityInfo(
            status=availability.status,
            required_plan=availability.required_plan,
            sources=sources,
        )
        logger.info(
            "[Digester:ApiType] SCIM availability for '%s': status=%s, required_plan=%s, sources=%s",
            application_name,
            availability.status.value,
            availability.required_plan or "-",
            ",".join(source.value for source in sources) or "-",
        )

    rest_availability_info: RestAvailabilityInfo | None = None
    if ApiType.REST in api_types:
        rest_availability = summarize_rest_availability(
            {
                DetectionSource.KNOWLEDGE: rest_knowledge_result,
                DetectionSource.WEB_SEARCH: rest_web_search_result,
            }
        )
        rest_contributing = set(rest_availability.sources)
        if rest_detected_in_docs:
            rest_contributing.add(DetectionSource.DOCUMENTATION)
        rest_sources = [source for source in DetectionSource if source in rest_contributing]
        rest_availability_info = RestAvailabilityInfo(
            status=rest_availability.status,
            required_plan=rest_availability.required_plan,
            sources=rest_sources,
        )
        logger.info(
            "[Digester:ApiType] REST availability for '%s': status=%s, required_plan=%s, sources=%s",
            application_name,
            rest_availability.status.value,
            rest_availability.required_plan or "-",
            ",".join(source.value for source in rest_sources) or "-",
        )

    merged_result = merge_info_metadata(
        all_info_candidates,
        total_items=len(doc_items),
        api_types=api_types,
        scim_availability=scim_availability_info,
        rest_availability=rest_availability_info,
    )
    await update_job_progress(job_id, stage="aggregation_finished", message="Extraction complete; finalizing")

    return {
        "result": merged_result,
        "relevantDocumentations": list(relevant_chunks_by_id.values()),
    }
