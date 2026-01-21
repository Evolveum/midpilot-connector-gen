#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

import logging
from importlib import resources
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, Union, cast
from uuid import UUID

from ...common.database.config import async_session_maker
from ...common.database.repositories.session_repository import SessionRepository
from ..digester.schema import EndpointsResponse, ObjectClassSchemaResponse, RelationsResponse
from .prompts.connIDPrompts import get_connID_system_prompt, get_connID_user_prompt
from .prompts.nativeSchemaPrompts import get_native_schema_system_prompt, get_native_schema_user_prompt
from .utils.api_type_helper import get_api_types_from_session
from .utils.generate_groovy import generate_groovy
from .utils.map_to_record import attributes_to_records_for_codegen
from .utils.operation_generators import (
    CreateGenerator,
    DeleteGenerator,
    RelationGenerator,
    SearchGenerator,
    UpdateGenerator,
)
from .utils.protocol_selectors import select_docs_path_for_protocol

logger = logging.getLogger(__name__)

AttributesPayload = Union[ObjectClassSchemaResponse, Mapping[str, Any]]
EndpointsPayload = Union[EndpointsResponse, Mapping[str, Any]]


def _read_adoc_text(package: str, filename: str) -> str:
    """
    Read a documentation .adoc file from package data using importlib.resources,
    which works both in dev and when packaged (wheel/zip).
    """
    try:
        with resources.files(package).joinpath(filename).open("r", encoding="utf-8") as fh:
            return fh.read()
    except Exception as e:
        logger.warning("Could not read resource %s/%s: %s", package, filename, e)
        return ""


def _attrs_map_from_payload(payload: AttributesPayload) -> Dict[str, Dict[str, Any]]:
    """
    Normalize attributes payload (pydantic model or mapping) into a dict[name] -> dict(info).
    """
    if isinstance(payload, ObjectClassSchemaResponse):
        attrs = payload.attributes or {}
        return {k: v.model_dump() for k, v in attrs.items()}

    if isinstance(payload, Mapping):
        if "attributes" in payload and isinstance(payload["attributes"], Mapping):
            return dict(cast(Mapping[str, Dict[str, Any]], payload["attributes"]))
        return dict(payload)  # already a flat map

    return {}


def _collect_pairs(val: Any) -> List[Tuple[int, Optional[str]]]:
    """
    Convert 'relevant_chunks' variants to a list of (chunk_index, doc_uuid_or_none).
    Supports:
      - [{'chunk_index': int, 'doc_uuid': '...'}, ...]
      - [int, int, ...]
      - None / unexpected -> []
    """
    out: List[Tuple[int, Optional[str]]] = []
    if not val:
        return out
    if isinstance(val, list) and val:
        first = val[0]
        if isinstance(first, dict) and "chunkIndex" in first:
            for item in val:
                if isinstance(item, dict):
                    idx = item.get("chunkIndex")
                    if isinstance(idx, int):
                        out.append((idx, cast(Optional[str], item.get("docUuid"))))
        else:
            for idx in val:
                if isinstance(idx, int):
                    out.append((idx, None))
    return out


def _merge_unique_pairs(*seqs: Iterable[Tuple[int, Optional[str]]]) -> List[Tuple[int, Optional[str]]]:
    """
    Merge multiple (idx, uuid) sequences preserving unique (idx, uuid) pairs.
    """
    merged: List[Tuple[int, Optional[str]]] = []
    seen: set[Tuple[int, Optional[str]]] = set()
    for seq in seqs:
        for idx, du in seq:
            pair = (idx, du)
            if pair not in seen:
                seen.add(pair)
                merged.append(pair)
    return merged


async def _collect_relevant_chunks(
    session_id: UUID, object_class: str, operation_name: str
) -> Tuple[Optional[List[int]], Optional[List[Dict[str, Any]]]]:
    """
    Collect relevant chunk indices and pairs from session for a given object class.

    Args:
        session_id: Session UUID
        object_class: Object class name
        operation_name: Operation name for logging (e.g., "Search", "Create")

    Returns:
        Tuple of (relevant_indices, relevant_pairs)
    """
    async with async_session_maker() as db:
        repo = SessionRepository(db)
        relevant_map = await repo.get_session_data(session_id, "relevantChunks")

    if not relevant_map:
        return None, None

    key_endpoints = f"{object_class}EndpointsOutput"
    key_attributes = f"{object_class}AttributesOutput"

    pairs_endpoints = _collect_pairs(relevant_map.get(key_endpoints))
    pairs_attributes = _collect_pairs(relevant_map.get(key_attributes))
    merged_pairs = _merge_unique_pairs(pairs_endpoints, pairs_attributes)

    if not merged_pairs:
        return None, None

    relevant_indices = [i for i, _ in merged_pairs]
    relevant_pairs = [{"chunkIndex": i, "docUuid": du} for i, du in merged_pairs if du]

    logger.info(
        "[Codegen:%s] Relevant chunks for endpoints=%d, for attributes=%d, merged=%d for %s",
        operation_name,
        len(pairs_endpoints),
        len(pairs_attributes),
        len(merged_pairs),
        object_class,
    )

    return relevant_indices, relevant_pairs


async def create_native_schema(
    attributes_payload: AttributesPayload,
    object_class: str,
    *,
    job_id: UUID,
) -> Dict[str, str]:
    """
    Generate Groovy for native schema mapping from attributes.
    """
    # packaged resource under codegen/documentations/
    user_schema_docs_text = _read_adoc_text(__package__ + ".documentations", "25-user-schema.adoc")

    attrs_map = _attrs_map_from_payload(attributes_payload)
    records = attributes_to_records_for_codegen(attrs_map)

    code = await generate_groovy(
        records=records,
        object_class=object_class,
        system_prompt=get_native_schema_system_prompt,
        user_prompt=get_native_schema_user_prompt,
        logger_prefix="NativeSchema",
        extra_prompt_vars={"user_schema_docs": user_schema_docs_text},
        job_id=job_id,
    )
    return {"code": code}


async def create_conn_id(
    attributes_payload: AttributesPayload,
    object_class: str,
    *,
    job_id: UUID,
) -> Dict[str, str]:
    """
    Generate Groovy for ConnID attribute mapping from attributes.
    """
    connid_docs_text = _read_adoc_text(__package__ + ".documentations", "30-attribute-to-connid-attributes.adoc")

    attrs_map = _attrs_map_from_payload(attributes_payload)
    records = attributes_to_records_for_codegen(attrs_map)

    code = await generate_groovy(
        records=records,
        object_class=object_class,
        system_prompt=get_connID_system_prompt,
        user_prompt=get_connID_user_prompt,
        logger_prefix="ConnID",
        extra_prompt_vars={"connID_docs": connid_docs_text},
        job_id=job_id,
    )
    return {"code": code}


async def create_search(
    *,
    attributes: AttributesPayload,
    endpoints: EndpointsPayload,
    documentation: Optional[str] = None,
    documentation_items: Optional[List[Dict[str, Any]]] = None,
    session_id: UUID,
    object_class: str,
    job_id: UUID,
) -> Dict[str, str]:
    """
    Generate the Groovy `search {}` block using relevant chunks + docs.
    Automatically selects protocol-specific prompts and documentation based on api_type.
    """
    # Get API types and select appropriate documentation
    api_types = await get_api_types_from_session(session_id)
    docs_path = select_docs_path_for_protocol("search", api_types)
    docs_text = _read_adoc_text(__package__ + ".documentations", docs_path)

    # Create generator with protocol-aware configuration
    generator = SearchGenerator(
        object_class=object_class,
        api_types=api_types,
        docs_text=docs_text,
    )

    # Collect relevant chunks
    relevant_indices, relevant_pairs = await _collect_relevant_chunks(session_id, object_class, "Search")

    # Generate code
    code = await generator.generate(
        session_id=session_id,
        documentation=documentation,
        documentation_items=documentation_items,
        relevant_chunk_indices=relevant_indices,
        relevant_chunk_pairs=relevant_pairs,
        job_id=job_id,
        attributes=attributes,
        endpoints=endpoints,
    )
    return {"code": code}


# Maybe we need better name for this def
async def create_create(
    *,
    attributes: AttributesPayload,
    endpoints: EndpointsPayload,
    documentation: Optional[str] = None,
    documentation_items: Optional[List[Dict[str, Any]]] = None,
    session_id: UUID,
    object_class: str,
    job_id: UUID,
) -> Dict[str, str]:
    """
    Generate the Groovy `create {}` block using relevant chunks + docs.
    Automatically selects protocol-specific prompts and documentation based on api_type.
    """
    # Get API types and select appropriate documentation
    api_types = await get_api_types_from_session(session_id)
    docs_path = select_docs_path_for_protocol("create", api_types)
    docs_text = _read_adoc_text(__package__ + ".documentations", docs_path)

    # Create generator with protocol-aware configuration
    generator = CreateGenerator(
        object_class=object_class,
        api_types=api_types,
        docs_text=docs_text,
    )

    # Collect relevant chunks
    relevant_indices, relevant_pairs = await _collect_relevant_chunks(session_id, object_class, "Create")

    # Generate code
    code = await generator.generate(
        session_id=session_id,
        documentation=documentation,
        documentation_items=documentation_items,
        relevant_chunk_indices=relevant_indices,
        relevant_chunk_pairs=relevant_pairs,
        job_id=job_id,
        attributes=attributes,
        endpoints=endpoints,
    )
    return {"code": code}


async def create_update(
    *,
    attributes: AttributesPayload,
    endpoints: EndpointsPayload,
    documentation: Optional[str] = None,
    documentation_items: Optional[List[Dict[str, Any]]] = None,
    session_id: UUID,
    object_class: str,
    job_id: UUID,
) -> Dict[str, str]:
    """
    Generate the Groovy `update {}` block using relevant chunks + docs.
    Automatically selects protocol-specific prompts and documentation based on api_type.
    """
    # Get API types and select appropriate documentation
    api_types = await get_api_types_from_session(session_id)
    docs_path = select_docs_path_for_protocol("update", api_types)
    docs_text = _read_adoc_text(__package__ + ".documentations", docs_path)

    # Create generator with protocol-aware configuration
    generator = UpdateGenerator(
        object_class=object_class,
        api_types=api_types,
        docs_text=docs_text,
    )

    # Collect relevant chunks
    relevant_indices, relevant_pairs = await _collect_relevant_chunks(session_id, object_class, "Update")

    # Generate code
    code = await generator.generate(
        session_id=session_id,
        documentation=documentation,
        documentation_items=documentation_items,
        relevant_chunk_indices=relevant_indices,
        relevant_chunk_pairs=relevant_pairs,
        job_id=job_id,
        attributes=attributes,
        endpoints=endpoints,
    )
    return {"code": code}


async def create_delete(
    *,
    attributes: AttributesPayload,
    endpoints: EndpointsPayload,
    documentation: Optional[str] = None,
    documentation_items: Optional[List[Dict[str, Any]]] = None,
    session_id: UUID,
    object_class: str,
    job_id: UUID,
) -> Dict[str, str]:
    """
    Generate the Groovy `delete {}` block using relevant chunks + docs.
    Automatically selects protocol-specific prompts and documentation based on api_type.
    """
    # Get API types and select appropriate documentation
    api_types = await get_api_types_from_session(session_id)
    docs_path = select_docs_path_for_protocol("delete", api_types)
    docs_text = _read_adoc_text(__package__ + ".documentations", docs_path)

    # Create generator with protocol-aware configuration
    generator = DeleteGenerator(
        object_class=object_class,
        api_types=api_types,
        docs_text=docs_text,
    )

    # Collect relevant chunks
    relevant_indices, relevant_pairs = await _collect_relevant_chunks(session_id, object_class, "Delete")

    # Generate code
    code = await generator.generate(
        session_id=session_id,
        documentation=documentation,
        documentation_items=documentation_items,
        relevant_chunk_indices=relevant_indices,
        relevant_chunk_pairs=relevant_pairs,
        job_id=job_id,
        attributes=attributes,
        endpoints=endpoints,
    )
    return {"code": code}


async def create_relation(
    *,
    relations: RelationsResponse,
    documentation: Optional[str] = None,
    documentation_items: Optional[List[Dict[str, Any]]] = None,
    session_id: UUID,
    job_id: UUID,
) -> Dict[str, str]:
    """
    Generate the Groovy `relation {}` block using relevant chunks + docs.
    """
    relation_docs_text = _read_adoc_text(__package__ + ".documentations" + ".rest", "50-relationship.adoc")

    relevant_indices: Optional[List[int]] = None
    relevant_pairs: Optional[List[Dict[str, Any]]] = None

    async with async_session_maker() as db:
        repo = SessionRepository(db)
        relevant_map = await repo.get_session_data(session_id, "relevantChunks")

    if relevant_map:
        raw = relevant_map.get("relationsOutput")
        pairs = _collect_pairs(raw)
        if pairs:
            relevant_indices = [i for i, _ in pairs]
            relevant_pairs = [{"chunkIndex": i, "docUuid": du} for i, du in pairs if du]
            logger.info(
                "[Codegen:Relation] Relevant chunks for indices_only=%d, pairs_with_uuid=%d",
                len(relevant_indices or []),
                len(relevant_pairs or []),
            )

    generator = RelationGenerator(docs_text=relation_docs_text)
    code = await generator.generate(
        session_id=session_id,
        documentation=documentation,
        documentation_items=documentation_items,
        relevant_chunk_indices=relevant_indices,
        relevant_chunk_pairs=relevant_pairs,
        job_id=job_id,
        relations=relations,
    )
    return {"code": code}
