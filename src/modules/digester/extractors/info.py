# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from src.modules.digester.aggregation.merges import is_empty_info_result_payload
from src.modules.digester.extraction.chunk_extraction import extract_single_chunk
from src.modules.digester.prompts.info_prompts import get_info_system_prompt, get_info_user_prompt
from src.modules.digester.schemas import InfoExtractionResponse, InfoMetadataExtraction

logger = logging.getLogger(__name__)


async def extract_info_metadata(
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
        payload = result.model_dump(by_alias=True)
        if is_empty_info_result_payload(payload) or result.info_metadata is None:
            return []
        return [result.info_metadata]

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
