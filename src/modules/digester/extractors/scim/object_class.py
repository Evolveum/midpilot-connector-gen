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

from src.common.jobs import update_job_progress
from src.modules.digester.prompts.scim.object_class_prompts import (
    scim_object_class_system_prompt,
    scim_object_class_user_prompt,
)
from src.modules.digester.schema import ObjectClass, ObjectClassesResponse
from src.modules.digester.scim.loader import get_base_scim_object_classes, load_scim_base_schemas
from src.modules.digester.utils.metadata_helper import build_doc_metadata_map
from src.modules.digester.utils.parallel import run_extraction_parallel
from src.modules.digester.utils.parallel_docs import process_documents_in_parallel

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
        - "relevantDocumentations": List of chunks containing custom extensions
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
    chunk_id_to_doc_id: Dict[str, str] = {}
    chunk_metadata_map = build_doc_metadata_map(doc_items)

    for item in doc_items:
        raw_chunk_id = item.get("chunkId")
        raw_doc_id = item.get("docId")
        if raw_chunk_id and raw_doc_id:
            chunk_id_to_doc_id[str(raw_chunk_id).strip()] = str(raw_doc_id).strip()

    # Load base schemas for LLM context
    scim_schemas = load_scim_base_schemas()

    # Create extractor function that includes SCIM schemas
    async def extractor_with_scim_schemas(content: str, job_id: UUID, chunk_id: UUID):
        chunk_metadata = chunk_metadata_map.get(str(chunk_id))
        custom_classes, has_relevant_data = await extract_custom_scim_classes(
            schema=content,
            job_id=job_id,
            chunk_id=chunk_id,
            scim_base_schemas=scim_schemas,
            chunk_metadata=chunk_metadata,
        )
        return custom_classes, has_relevant_data

    # Process all chunks in parallel
    results = await process_documents_in_parallel(
        chunk_items=doc_items,
        job_id=job_id,
        extractor=extractor_with_scim_schemas,
        logger_scope="SCIM:ObjectClasses",
    )

    # Collect results from all chunks
    for custom_classes, has_relevant_data, chunk_uuid in results:
        chunk_id = str(chunk_uuid)
        doc_id = chunk_id_to_doc_id.get(chunk_id)

        logger.info(
            "[SCIM:ObjectClasses] Chunk %s: extracted %d custom classes",
            chunk_id,
            len(custom_classes),
        )

        # Track chunks for each custom class
        for obj_class in custom_classes:
            class_name = obj_class.name.strip().lower()
            if class_name not in class_to_chunks:
                class_to_chunks[class_name] = []

            if obj_class.relevant_documentations:
                class_to_chunks[class_name].extend(obj_class.relevant_documentations)
            elif doc_id:
                class_to_chunks[class_name].append({"doc_id": doc_id, "chunk_id": chunk_id})
            else:
                logger.warning(
                    "[SCIM:ObjectClasses] Missing docId for chunk %s, skipping relevant chunk mapping for class %s",
                    chunk_id,
                    obj_class.name,
                )

        all_custom_classes.extend(custom_classes)

        if has_relevant_data and doc_id:
            all_relevant_chunks.append({"doc_id": doc_id, "chunk_id": chunk_id})
        elif has_relevant_data:
            logger.warning(
                "[SCIM:ObjectClasses] Missing docId for chunk %s, skipping top-level relevant chunk mapping",
                chunk_id,
            )

    logger.info(
        "[SCIM:ObjectClasses] Extracted %d custom classes from %d chunks",
        len(all_custom_classes),
        len(doc_items),
    )

    # Step 3: Find relevant chunks for base classes (User, Group)
    # Search documents for mentions of these standard SCIM classes
    await _find_relevant_chunks_for_base_classes(
        base_classes=base_classes,
        doc_items=doc_items,
        class_to_chunks=class_to_chunks,
    )

    # Step 4: Merge base + custom
    all_classes = base_classes + all_custom_classes

    # Step 5: Attach relevant chunks to merged classes
    for obj_class in all_classes:
        class_name = obj_class.name.strip().lower()
        if class_name in class_to_chunks:
            obj_class.relevant_documentations = class_to_chunks[class_name]

    # Create response
    result = ObjectClassesResponse(object_classes=all_classes)

    logger.info("[SCIM:ObjectClasses] Completed. Total classes: %d", len(all_classes))

    return {
        "result": result.model_dump(by_alias=True),
        "relevantDocumentations": all_relevant_chunks,
    }


async def _find_relevant_chunks_for_base_classes(
    base_classes: List[ObjectClass],
    doc_items: List[dict],
    class_to_chunks: Dict[str, List[Dict[str, Any]]],
) -> None:
    """
    Find relevant documentation chunks for base SCIM classes (User, Group).

    Searches through documentation to find mentions of standard SCIM resources
    and adds their {doc_id, chunk_id} references to class_to_chunks.

    Args:
        base_classes: List of base SCIM classes (User, Group)
        doc_items: List of documentation items
        class_to_chunks: Dictionary to populate with found chunks
    """
    logger.info("[SCIM:ObjectClasses] Finding relevant chunks for %d base classes", len(base_classes))

    for base_class in base_classes:
        class_name = base_class.name.strip().lower()
        if class_name not in class_to_chunks:
            class_to_chunks[class_name] = []

        # Search patterns for this class
        search_patterns = [
            f"/{base_class.name}s",  # e.g., /Users, /Groups
            f'"{base_class.name}"',  # Quoted in JSON/docs
            f"scim:schemas:core:2.0:{base_class.name}",  # SCIM URN
            f"{base_class.name} resource",  # Description text
            f"{base_class.name} endpoint",  # Endpoint documentation
        ]

        # Check each chunk for mentions
        for doc_item in doc_items:
            chunk_content = doc_item.get("content", "").lower()
            chunk_id = doc_item.get("chunkId")
            doc_id = doc_item.get("docId")

            if not chunk_id or not doc_id:
                continue

            # Check if any search pattern appears in the chunk content
            found = False
            for pattern in search_patterns:
                if pattern.lower() in chunk_content:
                    found = True
                    break

            if found:
                chunk_ref = {"doc_id": str(doc_id), "chunk_id": str(chunk_id)}
                if chunk_ref not in class_to_chunks[class_name]:
                    class_to_chunks[class_name].append(chunk_ref)
                    logger.debug(
                        "[SCIM:ObjectClasses] Found reference to %s in chunk %s",
                        base_class.name,
                        chunk_id,
                    )

    # Log results
    for base_class in base_classes:
        class_name = base_class.name.strip().lower()
        chunk_count = len(class_to_chunks.get(class_name, []))
        logger.info(
            "[SCIM:ObjectClasses] Base class '%s' found in %d chunks",
            base_class.name,
            chunk_count,
        )


async def extract_custom_scim_classes(
    schema: str,
    job_id: UUID,
    chunk_id: Optional[UUID] = None,
    chunk_metadata: Optional[Dict[str, Any]] = None,
    scim_base_schemas: Optional[Dict[str, Any]] = None,
) -> Tuple[List[ObjectClass], bool]:
    """
    Extract ONLY custom SCIM extensions and additional resources from a chunk.
    Does NOT extract standard User, Group, EnterpriseUser.

    Args:
        schema: The chunk content to extract from
        job_id: Job ID for progress tracking
        chunk_id: Optional chunk UUID
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
        chunk_id=chunk_id,
        track_chunk_per_item=True,
        chunk_metadata=chunk_metadata,
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
