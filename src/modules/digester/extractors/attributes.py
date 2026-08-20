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
from dataclasses import dataclass
from typing import Any, Dict, List
from uuid import UUID

from src.documents.filtering.filter import filter_documentation_items
from src.modules.digester.entities.object_classes import build_attribute_result, extract_attributes_from_result
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
from src.session.info_metadata import resolve_effective_api_type
from src.shared.enums import ApiType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _AttributeChunks:
    """Documentation context handed to a leaf (REST/SCIM) attribute extractor."""

    selected_content: List[str]
    chunk_ids: List[str]
    chunk_metadata_map: Dict[str, Any]
    chunk_id_to_doc_id: Dict[str, str]


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
    protocol = await resolve_effective_api_type(session_id, api_type_override)
    if protocol == ApiType.SQL:
        return await _extract_sql_attributes_flow(doc_items, object_class, session_id, job_id)

    is_scim = protocol == ApiType.SCIM

    chunks = _prepare_attribute_chunks(doc_items, relevant_chunks, object_class, is_scim)
    if chunks is None:
        return build_attribute_result()

    result = await _extract_attribute_leaf(is_scim, chunks, object_class, session_id, job_id)

    attributes_dict = extract_attributes_from_result(result)
    logger.info("[Digester:Attributes] Extracted %d attributes for %s", len(attributes_dict), object_class)

    if len(attributes_dict) == 0:
        logger.warning(
            "[Digester:Attributes] No attributes extracted for %s from relevant chunks, retrying with default criteria",
            object_class,
        )
        try:
            retry_result = await _retry_attributes_with_default_criteria(
                object_class,
                session_id,
                job_id,
                relevant_chunks,
                chunks.chunk_metadata_map,
                chunks.chunk_id_to_doc_id,
                is_scim=is_scim,
            )
        except Exception:
            logger.exception(
                "[Digester:Attributes] Exception while updating object class with attributes for %s", object_class
            )
            return result

        retry_attributes = extract_attributes_from_result(retry_result)
        if retry_attributes and retry_result is not None:
            logger.info(
                "[Digester:Attributes] Extracted %d attributes for %s on retry with default criteria",
                len(retry_attributes),
                object_class,
            )
            result = retry_result
            attributes_dict = retry_attributes

    await persist_object_class_field(session_id, object_class, "attributes", attributes_dict, "Digester:Attributes")

    return result


def _prepare_attribute_chunks(
    doc_items: List[dict],
    relevant_chunks: List[Dict[str, Any]],
    object_class: str,
    is_scim: bool,
) -> _AttributeChunks | None:
    """
    Build the documentation context for a leaf attribute extractor.

    Returns the prepared chunks, or ``None`` when a non-SCIM protocol has no usable
    documentation (the caller then returns an empty attribute result). SCIM tolerates
    empty content and falls back to schema heuristics, so it never returns ``None``.
    """
    if not doc_items:
        if is_scim:
            logger.info(
                "[Digester:Attributes] No documentation provided for SCIM %s; using schema heuristics",
                object_class,
            )
            return _AttributeChunks([], [], {}, {})
        logger.warning("[Digester:Attributes] No documentation provided for %s", object_class)
        return None

    if not relevant_chunks:
        if is_scim:
            logger.info(
                "[Digester:Attributes] No relevant chunks provided for SCIM %s; using schema heuristics",
                object_class,
            )
            return _AttributeChunks([], [], build_doc_metadata_map(doc_items), build_chunk_id_to_doc_id(doc_items))
        logger.warning("[Digester:Attributes] No relevant chunks provided for %s", object_class)
        return None

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
            logger.warning("[Digester:Attributes] No relevant chunks found for %s", object_class)
            return None

    return _AttributeChunks(
        selected_content,
        chunk_ids,
        build_doc_metadata_map(doc_items),
        build_chunk_id_to_doc_id(doc_items),
    )


async def _extract_attribute_leaf(
    is_scim: bool,
    chunks: _AttributeChunks,
    object_class: str,
    session_id: UUID,
    job_id: UUID,
) -> Dict[str, Any]:
    """Dispatch to the SCIM or REST leaf extractor with the prepared documentation context."""
    if is_scim:
        return await extract_scim_attributes(
            chunks.selected_content,
            object_class,
            job_id,
            session_id,
            chunks.chunk_ids,
            chunks.chunk_metadata_map,
            chunks.chunk_id_to_doc_id,
        )
    return await _extract_rest_attributes(
        chunks.selected_content,
        object_class,
        job_id,
        chunks.chunk_ids,
        chunks.chunk_metadata_map,
        chunks.chunk_id_to_doc_id,
    )


async def _extract_sql_attributes_flow(
    doc_items: List[dict],
    object_class: str,
    session_id: UUID,
    job_id: UUID,
) -> Dict[str, Any]:
    """SQL attribute extraction: extract from the schema, then persist onto the object class."""
    result = await extract_sql_attributes(doc_items, object_class, job_id)
    attributes_dict = extract_attributes_from_result(result)
    logger.info("[Digester:Attributes] Extracted %d SQL attributes for %s", len(attributes_dict), object_class)
    await persist_object_class_field(session_id, object_class, "attributes", attributes_dict, "Digester:Attributes")
    return result


async def _retry_attributes_with_default_criteria(
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

    fallback_result = await _extract_attribute_leaf(
        is_scim,
        _AttributeChunks(fallback_selected_content, fallback_chunk_ids, chunk_metadata_map, chunk_id_to_doc_id),
        object_class,
        session_id,
        job_id,
    )

    if not fallback_result:
        logger.info(
            "[Digester:Attributes] DEFAULT_CRITERIA chunks could not be selected for session %s; keeping empty attribute result",
            session_id,
        )

    return fallback_result
