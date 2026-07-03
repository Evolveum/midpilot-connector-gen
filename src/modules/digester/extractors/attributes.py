# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Attribute extraction workflow.

Owns the entity-level flow: resolve the effective protocol and dispatch to the
REST/SCIM/SQL leaf extractors, retry with broader documentation criteria when the
relevant chunks yield no attributes, and persist the extracted attributes back onto
the object class. Protocol-specific leaves live under ``extractors/rest``,
``extractors/scim`` and ``extractors/sql``.
"""

import logging
from typing import Any, Dict, List
from uuid import UUID

from src.common.chunk_filter.filter import filter_documentation_items
from src.common.enums import ApiType
from src.common.utils.session_info_metadata import resolve_effective_api_type
from src.modules.digester.entities.object_classes import extract_attributes_from_result
from src.modules.digester.extraction.metadata_helper import build_doc_metadata_map
from src.modules.digester.extractors.rest.attributes import extract_attributes as _extract_rest_attributes
from src.modules.digester.extractors.scim.attributes import extract_scim_attributes
from src.modules.digester.extractors.sql.attributes import extract_sql_attributes
from src.modules.digester.persistence import persist_object_class_field
from src.modules.digester.selection import (
    DEFAULT_CRITERIA,
    build_chunk_id_to_doc_id,
    build_relevant_chunks_from_doc_items,
    chunk_ids_from_relevant_chunks,
    exclude_doc_items_by_chunk_id,
    select_doc_chunks,
)

logger = logging.getLogger(__name__)


async def _retry_attributes_with_default_criteria(
    doc_items: List[dict],
    object_class: str,
    session_id: UUID,
    job_id: UUID,
    old_relevant_chunks: List[Dict[str, Any]],
    chunk_metadata_map: Dict[str, Any],
    chunk_id_to_doc_id: Dict[str, str],
    is_scim: bool = False,
) -> Dict[str, Any] | None:
    fallback_doc_items = await filter_documentation_items(DEFAULT_CRITERIA, session_id)
    if not fallback_doc_items:
        logger.info(
            "[Digester:Attributes] DEFAULT_CRITERIA matched no documentation for session %s; keeping empty attribute result",
            session_id,
        )
        return None

    primary_chunk_ids = chunk_ids_from_relevant_chunks(old_relevant_chunks)
    fallback_relevant_chunks = build_relevant_chunks_from_doc_items(fallback_doc_items)
    fallback_chunk_ids_set = chunk_ids_from_relevant_chunks(fallback_relevant_chunks)
    if primary_chunk_ids and primary_chunk_ids == fallback_chunk_ids_set:
        logger.info(
            "[Digester:Attributes] DEFAULT_CRITERIA matched same chunks for session %s, object class %s; skipping retry",
            session_id,
            object_class,
        )
        return None

    fallback_doc_items_filtered = exclude_doc_items_by_chunk_id(fallback_doc_items, primary_chunk_ids)
    fallback_relevant_chunks = build_relevant_chunks_from_doc_items(fallback_doc_items_filtered)
    if not fallback_relevant_chunks:
        logger.info(
            "[Digester:Attributes] DEFAULT_CRITERIA produced no new chunks for session %s; keeping empty attribute result",
            session_id,
        )
        return None

    fallback_selected_content, fallback_chunk_ids = select_doc_chunks(
        fallback_doc_items_filtered, fallback_relevant_chunks, "Digester:Attributes"
    )

    if is_scim:
        fallback_result = await extract_scim_attributes(
            fallback_selected_content,
            object_class,
            job_id,
            session_id,
            fallback_chunk_ids,
            chunk_metadata_map,
            chunk_id_to_doc_id,
        )
    else:
        fallback_result = await _extract_rest_attributes(
            fallback_selected_content,
            object_class,
            job_id,
            fallback_chunk_ids,
            chunk_metadata_map,
            chunk_id_to_doc_id,
        )

    if not fallback_result:
        logger.info(
            "[Digester:Attributes] DEFAULT_CRITERIA chunks could not be selected for session %s; keeping empty attribute result",
            session_id,
        )

    return fallback_result


async def extract_attributes(
    doc_items: List[dict],
    object_class: str,
    session_id: UUID,
    relevant_chunks: List[Dict[str, Any]],
    job_id: UUID,
    api_type_override: ApiType | None = None,
) -> Dict[str, Any]:
    """
    Extract attributes from only the relevant chunks of documentation and update the specific object class
    in objectClassesOutput with the extracted attributes.

    The extraction protocol (REST/SCIM/SQL) is taken from ``api_type_override`` when provided,
    otherwise it is derived from the apiType stored in the session ``infoMetadata``.

    Args:
        doc_items: Full documentation items
        object_class: Name of the object class
        session_id: Session ID
        relevant_chunks: List of {doc_id, chunk_id} dicts indicating which chunks to process
        job_id: Job ID for progress tracking
        api_type_override: Explicit protocol override; falls back to detected apiType when None
    """
    # TODO: Refactor this function
    protocol = await resolve_effective_api_type(session_id, api_type_override)
    if protocol == ApiType.SQL:
        result = await extract_sql_attributes(doc_items, object_class, job_id)
        attributes_dict = extract_attributes_from_result(result)
        logger.info("[Digester:Attributes] Extracted %d SQL attributes for %s", len(attributes_dict), object_class)
        await persist_object_class_field(session_id, object_class, "attributes", attributes_dict, "Digester:Attributes")
        return result

    is_scim = protocol == ApiType.SCIM

    if not doc_items:
        if is_scim:
            logger.info(
                "[Digester:Attributes] No documentation provided for SCIM %s; using schema heuristics",
                object_class,
            )
            selected_content: List[str] = []
            chunk_ids: List[str] = []
            chunk_metadata_map: Dict[str, Any] = {}
            chunk_id_to_doc_id: Dict[str, str] = {}
        else:
            logger.warning(f"[Digester:Attributes] No documentation provided for {object_class}")
            return {"result": {"attributes": {}}, "relevantDocumentations": []}
    elif not relevant_chunks:
        if is_scim:
            logger.info(
                "[Digester:Attributes] No relevant chunks provided for SCIM %s; using schema heuristics",
                object_class,
            )
            selected_content = []
            chunk_ids = []
            chunk_metadata_map = build_doc_metadata_map(doc_items)
            chunk_id_to_doc_id = build_chunk_id_to_doc_id(doc_items)
        else:
            logger.warning(f"[Digester:Attributes] No relevant chunks provided for {object_class}")
            return {"result": {"attributes": {}}, "relevantDocumentations": []}
    else:
        selected_content, chunk_ids = select_doc_chunks(doc_items, relevant_chunks, "Digester:Attributes")

        if not selected_content:
            if is_scim:
                logger.info(
                    "[Digester:Attributes] No selected documentation chunks for SCIM %s; using schema heuristics",
                    object_class,
                )
                selected_content = []
                chunk_ids = []
            else:
                logger.warning(f"[Digester:Attributes] No relevant chunks found for {object_class}")
                return {"result": {"attributes": {}}, "relevantDocumentations": []}

        chunk_metadata_map = build_doc_metadata_map(doc_items)
        chunk_id_to_doc_id = build_chunk_id_to_doc_id(doc_items)

    if is_scim:
        result = await extract_scim_attributes(
            selected_content,
            object_class,
            job_id,
            session_id,
            chunk_ids,
            chunk_metadata_map,
            chunk_id_to_doc_id,
        )
    else:
        result = await _extract_rest_attributes(
            selected_content,
            object_class,
            job_id,
            chunk_ids,
            chunk_metadata_map,
            chunk_id_to_doc_id,
        )

    attributes_dict = extract_attributes_from_result(result)
    logger.info("[Digester:Attributes] Extracted %d attributes for %s", len(attributes_dict), object_class)

    if len(attributes_dict) == 0:
        logger.warning(
            f"[Digester:Attributes] No attributes extracted for {object_class} from relevant chunks, retrying with default criteria"
        )
        try:
            # Retry with default criteria
            result_retry = await _retry_attributes_with_default_criteria(
                doc_items,
                object_class,
                session_id,
                job_id,
                relevant_chunks,
                chunk_metadata_map,
                chunk_id_to_doc_id,
                is_scim=is_scim,
            )
        except Exception:
            logger.exception(
                "[Digester:Attributes] Exception while updating object class with attributes for %s", object_class
            )
            return result

        attributes_dict_retry = extract_attributes_from_result(result_retry)
        if attributes_dict_retry and result_retry is not None:
            logger.info(
                "[Digester:Attributes] Extracted %d attributes for %s on retry with default criteria",
                len(attributes_dict_retry),
                object_class,
            )
            attributes_dict = attributes_dict_retry
            result = result_retry

    await persist_object_class_field(session_id, object_class, "attributes", attributes_dict, "Digester:Attributes")

    return result
