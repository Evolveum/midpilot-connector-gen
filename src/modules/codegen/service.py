# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, Union, cast
from uuid import UUID

from src.common.database.config import async_session_maker
from src.common.database.repositories.session_repository import SessionRepository
from src.common.enums import ApiType
from src.common.utils.session_info_metadata import get_session_api_types, get_session_base_api_url, is_scim_api
from src.modules.codegen.core.generate_groovy import generate_groovy
from src.modules.codegen.core.operations import (
    CreateGenerator,
    DeleteGenerator,
    RelationGenerator,
    SearchGenerator,
    UpdateGenerator,
)
from src.modules.codegen.enums import SearchIntent
from src.modules.codegen.prompts.connid_prompts import get_connID_system_prompt, get_connID_user_prompt
from src.modules.codegen.prompts.native_schema_prompts import (
    get_native_schema_system_prompt,
    get_native_schema_user_prompt,
)
from src.modules.codegen.selection.docs_loader import read_adoc_text
from src.modules.codegen.selection.protocol_selectors import get_operation_assets, get_search_operation_assets
from src.modules.codegen.utils.map_to_record import attributes_to_records_for_codegen
from src.modules.digester.schema import AttributeResponse, EndpointResponse, RelationsResponse

logger = logging.getLogger(__name__)

AttributesPayload = Union[AttributeResponse, Mapping[str, Any]]
EndpointsPayload = Union[EndpointResponse, Mapping[str, Any]]


def _attrs_map_from_payload(payload: AttributesPayload) -> Dict[str, Dict[str, Any]]:
    """
    Normalize attributes payload (pydantic model or mapping) into a dict[name] -> dict(info).
    """
    if isinstance(payload, AttributeResponse):
        attrs = payload.attributes or {}
        return {k: v.model_dump() for k, v in attrs.items()}

    if isinstance(payload, Mapping):
        if "attributes" in payload and isinstance(payload["attributes"], Mapping):
            return dict(cast(Mapping[str, Dict[str, Any]], payload["attributes"]))
        return dict(payload)  # already a flat map

    return {}


def _collect_pairs(val: Any) -> List[Tuple[int, Optional[str]]]:
    """
    Normalize relevant chunk references to ordered (index, chunk_id) tuples.
    """
    out: List[Tuple[int, Optional[str]]] = []
    if not val:
        return out
    if isinstance(val, list) and val:
        first = val[0]
        if isinstance(first, dict):
            for item in val:
                if not isinstance(item, dict):
                    continue
                chunk_id = item.get("chunk_id") or item.get("chunkId")
                if isinstance(chunk_id, str):
                    out.append((len(out), chunk_id))
        else:
            for idx in val:
                if isinstance(idx, int):
                    out.append((idx, None))
    return out


def _merge_unique_pairs(*seqs: Iterable[Tuple[int, Optional[str]]]) -> List[Tuple[int, Optional[str]]]:
    """
    Merge multiple (idx, uuid) sequences preserving unique chunk IDs.

    When a chunk_id is present, deduplicate by chunk_id only so the same
    documentation chunk is not processed multiple times if it was selected
    from both attributes and endpoints. For legacy index-only entries
    (chunk_id is None), preserve uniqueness by the full pair.
    """
    merged: List[Tuple[int, Optional[str]]] = []
    seen_pairs: set[Tuple[int, Optional[str]]] = set()
    seen_chunk_ids: set[str] = set()
    for seq in seqs:
        for idx, chunk_id in seq:
            if isinstance(chunk_id, str):
                if chunk_id in seen_chunk_ids:
                    continue
                seen_chunk_ids.add(chunk_id)
                merged.append((idx, chunk_id))
                continue

            pair = (idx, chunk_id)
            if pair not in seen_pairs:
                seen_pairs.add(pair)
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
        relevant_map = await repo.get_session_data(session_id, "relevantDocumentations")

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
    relevant_pairs = [{"chunk_id": chunk_id} for _, chunk_id in merged_pairs if chunk_id]

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
    session_id: UUID,
    job_id: UUID,
) -> Dict[str, str]:
    """
    Generate Groovy for native schema mapping from attributes.
    """

    api_types = await get_session_api_types(session_id)
    protocol = ApiType.SCIM if is_scim_api(api_types) else ApiType.REST
    assets = get_operation_assets("native_schema", protocol)
    docs_text = read_adoc_text(__package__ + ".documentations", assets.docs_path)

    attrs_map = _attrs_map_from_payload(attributes_payload)
    records = attributes_to_records_for_codegen(attrs_map)

    code = await generate_groovy(
        records=records,
        object_class=object_class,
        system_prompt=get_native_schema_system_prompt,
        user_prompt=get_native_schema_user_prompt,
        logger_prefix="NativeSchema",
        extra_prompt_vars={"user_schema_docs": docs_text},
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
    docs_text = read_adoc_text(__package__ + ".documentations" + ".rest", "30-attribute-to-connid-attributes.adoc")

    attrs_map = _attrs_map_from_payload(attributes_payload)
    records = attributes_to_records_for_codegen(attrs_map)

    code = await generate_groovy(
        records=records,
        object_class=object_class,
        system_prompt=get_connID_system_prompt,
        user_prompt=get_connID_user_prompt,
        logger_prefix="ConnID",
        extra_prompt_vars={"connID_docs": docs_text},
        job_id=job_id,
    )
    return {"code": code}


async def create_search(
    *,
    attributes: AttributesPayload,
    endpoints: Optional[EndpointsPayload] = None,
    preferred_endpoints: Optional[List[Dict[str, Any]]] = None,
    session_id: UUID,
    object_class: str,
    intent: SearchIntent,
    job_id: UUID,
) -> Dict[str, str]:
    """
    Generate the Groovy `search {}` block using relevant chunks + docs.
    Automatically selects protocol-specific prompts and documentation based on api_type.
    """
    # Get API types and select appropriate documentation
    api_types = await get_session_api_types(session_id)
    protocol = ApiType.SCIM if is_scim_api(api_types) else ApiType.REST
    assets = get_search_operation_assets(protocol, intent)
    docs_text = read_adoc_text(__package__ + ".documentations", assets.docs_path)
    base_api_url = await get_session_base_api_url(session_id)

    generator = SearchGenerator(
        object_class=object_class,
        intent=intent,
        preferred_endpoints=preferred_endpoints,
        docs_text=docs_text,
        system_prompt=assets.system_prompt,
        user_prompt=assets.user_prompt,
        protocol_label=protocol.name,
        base_api_url=base_api_url,
    )

    # Collect relevant chunks
    relevant_indices, relevant_pairs = await _collect_relevant_chunks(session_id, object_class, "Search")

    # Generate code
    code = await generator.generate(
        session_id=session_id,
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
    endpoints: Optional[EndpointsPayload] = None,
    preferred_endpoints: Optional[List[Dict[str, Any]]] = None,
    session_id: UUID,
    object_class: str,
    job_id: UUID,
) -> Dict[str, str]:
    """
    Generate the Groovy `create {}` block using relevant chunks + docs.
    Automatically selects protocol-specific prompts and documentation based on api_type.
    """
    # Get API types and select appropriate documentation
    api_types = await get_session_api_types(session_id)
    protocol = ApiType.SCIM if is_scim_api(api_types) else ApiType.REST
    assets = get_operation_assets("create", protocol)
    docs_text = read_adoc_text(__package__ + ".documentations", assets.docs_path)
    base_api_url = await get_session_base_api_url(session_id)

    generator = CreateGenerator(
        object_class=object_class,
        preferred_endpoints=preferred_endpoints,
        docs_text=docs_text,
        system_prompt=assets.system_prompt,
        user_prompt=assets.user_prompt,
        protocol_label=protocol.name,
        base_api_url=base_api_url,
    )

    # Collect relevant chunks
    relevant_indices, relevant_pairs = await _collect_relevant_chunks(session_id, object_class, "Create")

    # Generate code
    code = await generator.generate(
        session_id=session_id,
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
    endpoints: Optional[EndpointsPayload] = None,
    preferred_endpoints: Optional[List[Dict[str, Any]]] = None,
    session_id: UUID,
    object_class: str,
    job_id: UUID,
) -> Dict[str, str]:
    """
    Generate the Groovy `update {}` block using relevant chunks + docs.
    Automatically selects protocol-specific prompts and documentation based on api_type.
    """
    # Get API types and select appropriate documentation
    api_types = await get_session_api_types(session_id)
    protocol = ApiType.SCIM if is_scim_api(api_types) else ApiType.REST
    assets = get_operation_assets("update", protocol)
    docs_text = read_adoc_text(__package__ + ".documentations", assets.docs_path)
    base_api_url = await get_session_base_api_url(session_id)

    generator = UpdateGenerator(
        object_class=object_class,
        preferred_endpoints=preferred_endpoints,
        docs_text=docs_text,
        system_prompt=assets.system_prompt,
        user_prompt=assets.user_prompt,
        protocol_label=protocol.name,
        base_api_url=base_api_url,
    )

    # Collect relevant chunks
    relevant_indices, relevant_pairs = await _collect_relevant_chunks(session_id, object_class, "Update")

    # Generate code
    code = await generator.generate(
        session_id=session_id,
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
    endpoints: Optional[EndpointsPayload] = None,
    preferred_endpoints: Optional[List[Dict[str, Any]]] = None,
    session_id: UUID,
    object_class: str,
    job_id: UUID,
) -> Dict[str, str]:
    """
    Generate the Groovy `delete {}` block using relevant chunks + docs.
    Automatically selects protocol-specific prompts and documentation based on api_type.
    """
    # Get API types and select appropriate documentation
    api_types = await get_session_api_types(session_id)
    protocol = ApiType.SCIM if is_scim_api(api_types) else ApiType.REST
    assets = get_operation_assets("delete", protocol)
    docs_text = read_adoc_text(__package__ + ".documentations", assets.docs_path)
    base_api_url = await get_session_base_api_url(session_id)

    generator = DeleteGenerator(
        object_class=object_class,
        preferred_endpoints=preferred_endpoints,
        docs_text=docs_text,
        system_prompt=assets.system_prompt,
        user_prompt=assets.user_prompt,
        protocol_label=protocol.name,
        base_api_url=base_api_url,
    )

    # Collect relevant chunks
    relevant_indices, relevant_pairs = await _collect_relevant_chunks(session_id, object_class, "Delete")

    # Generate code
    code = await generator.generate(
        session_id=session_id,
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
    session_id: UUID,
    job_id: UUID,
) -> Dict[str, str]:
    """
    Generate the Groovy `relation {}` block using relevant chunks + docs.
    """
    relation_docs_text = read_adoc_text(__package__ + ".documentations" + ".rest", "50-relationship.adoc")

    relevant_indices: Optional[List[int]] = None
    relevant_pairs: Optional[List[Dict[str, Any]]] = None

    async with async_session_maker() as db:
        repo = SessionRepository(db)
        relevant_map = await repo.get_session_data(session_id, "relevantDocumentations")

    if relevant_map:
        raw = relevant_map.get("relationsOutput")
        pairs = _collect_pairs(raw)
        if pairs:
            relevant_indices = [i for i, _ in pairs]
            relevant_pairs = [{"chunk_id": chunk_id} for _, chunk_id in pairs if chunk_id]
            logger.info(
                "[Codegen:Relation] Relevant chunks for indices_only=%d, pairs_with_uuid=%d",
                len(relevant_indices or []),
                len(relevant_pairs or []),
            )

    generator = RelationGenerator(docs_text=relation_docs_text)
    code = await generator.generate(
        session_id=session_id,
        relevant_chunk_indices=relevant_indices,
        relevant_chunk_pairs=relevant_pairs,
        job_id=job_id,
        relations=relations,
    )
    return {"code": code}
