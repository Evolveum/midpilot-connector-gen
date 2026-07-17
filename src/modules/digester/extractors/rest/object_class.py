# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from src.modules.digester.extraction.chunk_extraction import build_chunk_extraction_chain, extract_single_chunk
from src.modules.digester.prompts.rest.object_class_prompts import (
    get_object_class_system_prompt,
    get_object_class_user_prompt,
)
from src.modules.digester.schemas import (
    ExtendedObjectClass,
    ObjectClassesExtendedResponse,
)

logger = logging.getLogger(__name__)


def build_object_class_extraction_chain() -> Any:
    """Build the reusable chain for REST object-class extraction across chunks."""
    return build_chunk_extraction_chain(
        pydantic_model=ObjectClassesExtendedResponse,
        system_prompt=get_object_class_system_prompt,
        user_prompt=get_object_class_user_prompt,
    )


async def extract_object_classes_raw(
    schema: str,
    job_id: UUID,
    chunk_id: Optional[UUID] = None,
    chunk_metadata: Optional[Dict[str, Any]] = None,
    extraction_chain: Any | None = None,
) -> Tuple[List[ExtendedObjectClass], bool]:
    """
    Extract raw object classes from a single chunk with one LLM call.
    Does NOT deduplicate or sort - that's done later across all chunks.

    Args:
        schema: Chunk content to extract from.
        job_id: Job ID for progress tracking.
        chunk_id: Optional chunk UUID.
        chunk_metadata: Optional metadata for summary/tag prompt context.
        extraction_chain: Optional pre-built reusable extraction chain.
    """

    def parse_fn(result: ObjectClassesExtendedResponse) -> List[ExtendedObjectClass]:
        return result.objectClasses or []

    extracted, has_relevant_data = await extract_single_chunk(
        schema=schema,
        pydantic_model=ObjectClassesExtendedResponse,
        system_prompt=get_object_class_system_prompt,
        user_prompt=get_object_class_user_prompt,
        parse_fn=parse_fn,
        logger_prefix="[Digester:REST:ObjectClasses] ",
        job_id=job_id,
        chunk_id=chunk_id,
        track_chunk_per_item=True,
        chunk_metadata=chunk_metadata,
        extraction_chain=extraction_chain,
    )

    extracted_valid: List[ExtendedObjectClass] = []

    # Validate extracted object classes by checking if names exist in the schema
    for obj_class in extracted:
        if obj_class.name and obj_class.name.strip():
            if re.search(re.escape(obj_class.name.strip()) + r'[\s\n\t.,;:!?\-\)\]\}"\']', schema, re.IGNORECASE):
                extracted_valid.append(obj_class)
            else:
                logger.info(
                    "[Digester:ObjectClasses] Extracted object class name '%s' not found in chunk, deleting object class",
                    obj_class.name,
                )

    logger.info("[Digester:ObjectClasses] Raw extraction complete from chunk. Count: %d", len(extracted_valid))
    return extracted_valid, bool(extracted_valid)
