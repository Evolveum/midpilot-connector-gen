# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from src.modules.digester.extraction.chunk_extraction import extract_single_chunk
from src.modules.digester.prompts.apitype.documentation_prompts import (
    get_api_type_system_prompt,
    get_api_type_user_prompt,
)
from src.modules.digester.schemas import ApiTypeResponse

logger = logging.getLogger(__name__)


async def extract_api_type(
    schema: str,
    job_id: UUID,
    chunk_id: Optional[UUID] = None,
    chunk_metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[List[ApiTypeResponse], bool]:
    """
    Detect the API protocol type(s) from a single chunk with a standalone LLM call.

    This is a dedicated extractor, separate from the generic info metadata extraction,
    so apiType classification has its own prompt and structured output. Aggregation
    across chunks is handled in the service layer.

    Returns:
        - List of extracted ApiTypeResponse candidates (0 or 1 item)
        - Boolean indicating if relevant data was found
    """

    def parse_fn(result: ApiTypeResponse) -> List[ApiTypeResponse]:
        if not result.api_type:
            return []
        return [result]

    extracted, has_relevant_data = await extract_single_chunk(
        schema=schema,
        pydantic_model=ApiTypeResponse,
        system_prompt=get_api_type_system_prompt,
        user_prompt=get_api_type_user_prompt,
        parse_fn=parse_fn,
        logger_prefix="[Digester:ApiType] ",
        job_id=job_id,
        chunk_id=chunk_id,
        chunk_metadata=chunk_metadata,
    )

    logger.info(
        "[Digester:ApiType] Extraction complete. has_relevant_data=%s",
        has_relevant_data,
    )
    return extracted, has_relevant_data
