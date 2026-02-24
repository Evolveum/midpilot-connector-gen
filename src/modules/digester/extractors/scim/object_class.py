# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
SCIM 2.0 guided object class extraction.

This module extracts ONLY custom extensions and additional resources
beyond the standard SCIM User, Group, and EnterpriseUser classes.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from .....common.jobs import update_job_progress
from ...prompts.scim.object_class_prompts import (
    scim_object_class_system_prompt,
    scim_object_class_user_prompt,
)
from ...schema import ObjectClass, ObjectClassesResponse
from ...scim.loader import get_base_scim_object_classes, load_scim_base_schemas
from ...utils.parallel import run_extraction_parallel
from ...utils.parallel_docs import process_documents_in_parallel

logger = logging.getLogger(__name__)


async def extract_scim_object_classes(
    doc_items: List[dict],
    job_id: UUID,
) -> Dict[str, Any]:
    """
    Extract SCIM object classes using guided approach:
    1. Start with SCIM base classes (User, Group, EnterpriseUser)
    2. Extract only custom extensions from documentation
    3. Merge base + custom

    Args:
        doc_items: List of documentation items to process
        job_id: Job ID for progress tracking

    Returns:
        Dictionary with:
        - "result": ObjectClassesResponse with merged classes
        - "relevantChunks": List of chunks containing custom extensions
    """
    logger.info("[SCIM:ObjectClasses] Starting guided extraction")

    # Update job progress with total documents
    total_docs = len(doc_items)
    await update_job_progress(
        job_id, total_processing=total_docs, processing_completed=0, message="Processing SCIM documents"
    )

    # Step 1: Load SCIM base object classes
    base_classes_data = get_base_scim_object_classes()
    base_classes = [
        ObjectClass(
            name=cls["name"],
            relevant="true",
            superclass=cls.get("superclass"),
            abstract=cls.get("abstract", False),
            embedded=cls.get("embedded", False),
            description=cls["description"],
        )
        for cls in base_classes_data
    ]

    logger.info("[SCIM:ObjectClasses] Loaded %d base SCIM classes", len(base_classes))

    # Step 2: Extract custom extensions from documentation
    all_custom_classes: List[ObjectClass] = []
    all_relevant_chunks: List[Dict[str, Any]] = []
    class_to_chunks: Dict[str, List[Dict[str, Any]]] = {}

    # Load base schemas for LLM context
    scim_schemas = load_scim_base_schemas()

    # Create extractor function that includes SCIM schemas
    async def extractor_with_scim_schemas(content: str, job_id: UUID, doc_uuid: UUID):
        custom_classes, has_relevant_data = await extract_custom_scim_classes(
            schema=content,
            job_id=job_id,
            doc_id=doc_uuid,
            scim_base_schemas=scim_schemas,
        )
        return custom_classes, has_relevant_data

    # Process all documents in parallel
    results = await process_documents_in_parallel(
        doc_items=doc_items,
        job_id=job_id,
        extractor=extractor_with_scim_schemas,
        logger_scope="SCIM:ObjectClasses",
    )

    # Collect results from all documents
    for custom_classes, has_relevant_data, doc_uuid in results:
        logger.info(
            "[SCIM:ObjectClasses] Document %s: extracted %d custom classes",
            doc_uuid,
            len(custom_classes),
        )

        # Track chunks for each custom class
        for obj_class in custom_classes:
            class_name = obj_class.name.strip().lower()
            if class_name not in class_to_chunks:
                class_to_chunks[class_name] = []

            if obj_class.relevant_chunks:
                class_to_chunks[class_name].extend(obj_class.relevant_chunks)
            else:
                class_to_chunks[class_name].append({"docUuid": str(doc_uuid)})

        all_custom_classes.extend(custom_classes)

        if has_relevant_data:
            all_relevant_chunks.append({"docUuid": str(doc_uuid)})

    logger.info(
        "[SCIM:ObjectClasses] Extracted %d custom classes from %d documents",
        len(all_custom_classes),
        len(doc_items),
    )

    # Step 3: Merge base + custom
    all_classes = base_classes + all_custom_classes

    # Step 4: Attach relevant chunks to merged classes
    for obj_class in all_classes:
        class_name = obj_class.name.strip().lower()
        if class_name in class_to_chunks:
            obj_class.relevant_chunks = class_to_chunks[class_name]

    # Create response
    result = ObjectClassesResponse(object_classes=all_classes)

    logger.info("[SCIM:ObjectClasses] Completed. Total classes: %d", len(all_classes))

    return {
        "result": result.model_dump(by_alias=True),
        "relevantChunks": all_relevant_chunks,
    }


async def extract_custom_scim_classes(
    schema: str,
    job_id: UUID,
    doc_id: Optional[UUID] = None,
    doc_metadata: Optional[Dict[str, Any]] = None,
    scim_base_schemas: Optional[Dict[str, Any]] = None,
) -> Tuple[List[ObjectClass], bool]:
    """
    Extract ONLY custom SCIM extensions and additional resources from a document.
    Does NOT extract standard User, Group, EnterpriseUser.

    Args:
        schema: The document content to extract from
        job_id: Job ID for progress tracking
        doc_id: Optional document UUID
        scim_base_schemas: Optional SCIM base schemas for LLM context

    Returns:
        - List of custom ObjectClass instances
        - Boolean indicating if relevant data was found
    """

    def parse_fn(result: ObjectClassesResponse) -> List[ObjectClass]:
        return result.objectClasses or []

    extracted, has_relevant_data = await run_extraction_parallel(
        schema=schema,
        pydantic_model=ObjectClassesResponse,
        system_prompt=scim_object_class_system_prompt,
        user_prompt=scim_object_class_user_prompt,
        parse_fn=parse_fn,
        logger_prefix="[SCIM:ObjectClasses] ",
        job_id=job_id,
        doc_id=doc_id,
        track_chunk_per_item=True,
        chunk_metadata=doc_metadata,
    )

    # Filter out any standard SCIM classes that LLM might have mistakenly extracted
    custom_only: List[ObjectClass] = []
    standard_classes = {"user", "group", "enterpriseuser"}

    for obj_class in extracted:
        class_name_lower = obj_class.name.strip().lower()
        if class_name_lower not in standard_classes:
            custom_only.append(obj_class)
        else:
            logger.info(
                "[SCIM:ObjectClasses] Filtered out standard class '%s' (should not be extracted)",
                obj_class.name,
            )

    logger.info(
        "[SCIM:ObjectClasses] Custom extraction complete. Count: %d (filtered %d standard classes)",
        len(custom_only),
        len(extracted) - len(custom_only),
    )

    return custom_only, bool(custom_only)
