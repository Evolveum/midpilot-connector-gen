# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from ..prompts.info_prompts import get_info_system_prompt, get_info_user_prompt
from ..schema import InfoMetadata, InfoResponse
from ..utils.merges import is_empty_info_result_payload
from ..utils.parallel import run_extraction_parallel

logger = logging.getLogger(__name__)


async def extract_info_metadata(
    schema: str,
    job_id: UUID,
    chunk_id: Optional[UUID] = None,
    chunk_metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[List[InfoMetadata], bool]:
    """
    Extract raw info metadata from a single chunk with a standalone LLM call.
    Does NOT aggregate across chunks - aggregation is handled in the service layer.

    Returns:
        - List of extracted InfoMetadata candidates (0 or 1 item)
        - Boolean indicating if relevant data was found
    """

    def parse_fn(result: InfoResponse) -> List[InfoMetadata]:
        payload = result.model_dump(by_alias=True)
        if is_empty_info_result_payload(payload) or result.info_metadata is None:
            return []
        return [result.info_metadata]

    extracted, has_relevant_data = await run_extraction_parallel(
        schema=schema,
        pydantic_model=InfoResponse,
        system_prompt=get_info_system_prompt,
        user_prompt=get_info_user_prompt,
        parse_fn=parse_fn,
        logger_prefix="[Digester:InfoMetadata] ",
        job_id=job_id,
        chunk_id=chunk_id,
        chunk_metadata=chunk_metadata,
    )

    logger.info("[Digester:InfoMetadata] Raw extraction complete from chunk. Count: %d", len(extracted))
    return extracted, has_relevant_data
