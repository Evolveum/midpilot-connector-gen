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
from .utils.generate_groovy import generate_groovy
from .utils.map_to_record import attributes_to_records_for_codegen
from .utils.operation_generators import (
    CreateGenerator,
    DeleteGenerator,
    RelationGenerator,
    SearchGenerator,
    UpdateGenerator,
)

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
    If session_id is provided, attempts to use pre-chunked 'relevant_chunks' from the session.
    """
    search_docs_text = _read_adoc_text(__package__ + ".documentations", "40-search-users.adoc")

    relevant_indices: Optional[List[int]] = None
    relevant_pairs: Optional[List[Dict[str, Any]]] = None

    async with async_session_maker() as db:
        repo = SessionRepository(db)
        relevant_map = await repo.get_session_data(session_id, "relevantChunks")

    if relevant_map:
        key_endpoints = f"{object_class}EndpointsOutput"
        key_attributes = f"{object_class}AttributesOutput"

        pairs_endpoints = _collect_pairs(relevant_map.get(key_endpoints))
        pairs_attributes = _collect_pairs(relevant_map.get(key_attributes))
        merged_pairs = _merge_unique_pairs(pairs_endpoints, pairs_attributes)

        if merged_pairs:
            relevant_indices = [i for i, _ in merged_pairs]
            # include uuid-only entries for downstream per-document selection
            relevant_pairs = [{"chunkIndex": i, "docUuid": du} for i, du in merged_pairs if du]
            logger.info(
                "[Codegen:Search] Relevant chunks for endpoints=%d, for attributes=%d, merged=%d for %s",
                len(pairs_endpoints),
                len(pairs_attributes),
                len(merged_pairs),
                object_class,
            )
            try:
                logger.info("[Codegen:Search] Relevant details for %s: %s", object_class, merged_pairs)
            except Exception:
                pass

    generator = SearchGenerator(object_class=object_class, extra_prompt_vars={"search_docs": search_docs_text})
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
    """
    create_docs_text = _read_adoc_text(
        __package__ + ".documentations", "50-create.adoc"
    )  # No documentation yet, will be added later

    relevant_indices: Optional[List[int]] = None
    relevant_pairs: Optional[List[Dict[str, Any]]] = None

    async with async_session_maker() as db:
        repo = SessionRepository(db)
        relevant_map = await repo.get_session_data(session_id, "relevantChunks")

    if relevant_map:
        key_endpoints = f"{object_class}EndpointsOutput"
        key_attributes = f"{object_class}AttributesOutput"

        pairs_endpoints = _collect_pairs(relevant_map.get(key_endpoints))
        pairs_attributes = _collect_pairs(relevant_map.get(key_attributes))
        merged_pairs = _merge_unique_pairs(pairs_endpoints, pairs_attributes)

        if merged_pairs:
            relevant_indices = [i for i, _ in merged_pairs]
            relevant_pairs = [{"chunkIndex": i, "docUuid": du} for i, du in merged_pairs if du]
            logger.info(
                "[Codegen:Create] Relevant chunks for endpoints=%d, for attributes=%d, merged=%d for %s",
                len(pairs_endpoints),
                len(pairs_attributes),
                len(merged_pairs),
                object_class,
            )

    generator = CreateGenerator(object_class=object_class, extra_prompt_vars={"create_docs": create_docs_text})
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
    """
    update_docs_text = _read_adoc_text(
        __package__ + ".documentations", "60-update.adoc"
    )  # No documentation yet, will be added later

    relevant_indices: Optional[List[int]] = None
    relevant_pairs: Optional[List[Dict[str, Any]]] = None

    async with async_session_maker() as db:
        repo = SessionRepository(db)
        relevant_map = await repo.get_session_data(session_id, "relevantChunks")

    if relevant_map:
        key_endpoints = f"{object_class}EndpointsOutput"
        key_attributes = f"{object_class}AttributesOutput"

        pairs_endpoints = _collect_pairs(relevant_map.get(key_endpoints))
        pairs_attributes = _collect_pairs(relevant_map.get(key_attributes))
        merged_pairs = _merge_unique_pairs(pairs_endpoints, pairs_attributes)

        if merged_pairs:
            relevant_indices = [i for i, _ in merged_pairs]
            relevant_pairs = [{"chunkIndex": i, "docUuid": du} for i, du in merged_pairs if du]
            logger.info(
                "[Codegen:Update] Relevant chunks for endpoints=%d, for attributes=%d, merged=%d for %s",
                len(pairs_endpoints),
                len(pairs_attributes),
                len(merged_pairs),
                object_class,
            )

    generator = UpdateGenerator(object_class=object_class, extra_prompt_vars={"update_docs": update_docs_text})
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
    """
    delete_docs_text = _read_adoc_text(
        __package__ + ".documentations", "70-delete.adoc"
    )  # No documentation yet, will be added later

    relevant_indices: Optional[List[int]] = None
    relevant_pairs: Optional[List[Dict[str, Any]]] = None

    async with async_session_maker() as db:
        repo = SessionRepository(db)
        relevant_map = await repo.get_session_data(session_id, "relevantChunks")

    if relevant_map:
        key_endpoints = f"{object_class}EndpointsOutput"
        key_attributes = f"{object_class}AttributesOutput"

        pairs_endpoints = _collect_pairs(relevant_map.get(key_endpoints))
        pairs_attributes = _collect_pairs(relevant_map.get(key_attributes))
        merged_pairs = _merge_unique_pairs(pairs_endpoints, pairs_attributes)

        if merged_pairs:
            relevant_indices = [i for i, _ in merged_pairs]
            relevant_pairs = [{"chunkIndex": i, "docUuid": du} for i, du in merged_pairs if du]
            logger.info(
                "[Codegen:Delete] Relevant chunks for endpoints=%d, for attributes=%d, merged=%d for %s",
                len(pairs_endpoints),
                len(pairs_attributes),
                len(merged_pairs),
                object_class,
            )

    generator = DeleteGenerator(object_class=object_class, extra_prompt_vars={"delete_docs": delete_docs_text})
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
    relation_docs_text = _read_adoc_text(__package__ + ".documentations", "50-relationship.adoc")

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

    generator = RelationGenerator(extra_prompt_vars={"relation_docs": relation_docs_text})
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
