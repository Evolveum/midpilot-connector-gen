# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, cast
from uuid import UUID

from src.common.database.config import async_session_maker
from src.common.database.repositories.relevant_chunk_repository import RelevantChunkRepository
from src.common.enums import ApiType
from src.common.utils.normalize import normalize_object_class_name
from src.common.utils.session_info_metadata import (
    get_session_base_api_url,
    get_session_connection_target,
)
from src.modules.codegen.core.generate_groovy import generate_groovy
from src.modules.codegen.core.operations import (
    AuthorizationGenerator,
    CreateGenerator,
    DeleteGenerator,
    RelationGenerator,
    SearchGenerator,
    UpdateGenerator,
    build_other_authorization_scaffold,
)
from src.modules.codegen.enums import SearchIntent
from src.modules.codegen.prompts.connid_prompts import get_connID_system_prompt, get_connID_user_prompt
from src.modules.codegen.prompts.native_schema_prompts import (
    get_native_schema_system_prompt,
    get_native_schema_user_prompt,
)
from src.modules.codegen.schema import AttributesPayload, AuthPayload, CodegenRepairContext, EndpointsPayload
from src.modules.codegen.selection.authorization import (
    enrich_preferred_authorizations,
    has_matching_preferred_authorization,
    is_single_other_authorization,
    prepare_preferred_authorizations_for_generation,
    select_authorization_chunk_refs,
)
from src.modules.codegen.selection.docs_loader import load_required_adoc_text
from src.modules.codegen.selection.protocol_selectors import get_operation_assets, get_search_operation_assets
from src.modules.codegen.utils.map_to_record import attributes_to_records_for_codegen
from src.modules.digester.schemas import AttributeResponse, RelationsResponse

logger = logging.getLogger(__name__)


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
                    raw_sequence = item.get("relevant_sequence") or item.get("relevantSequence")
                    if raw_sequence is None:
                        sequence = len(out)
                    else:
                        try:
                            sequence = int(raw_sequence)
                        except Exception:
                            sequence = len(out)
                    out.append((sequence, chunk_id))
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


async def _collect_relation_object_class_pairs(
    relations: RelationsResponse,
    session_id: UUID,
) -> List[Dict[str, str]]:
    """
    Select object-class documentation chunks for the relation subject and object.
    """
    if not relations.relations:
        return []

    async with async_session_maker() as db:
        repo = RelevantChunkRepository(db)
        chunk_map = await repo.get_relevant_chunks_grouped_by_entity(
            session_id=session_id,
            result_key="objectClassesOutput",
        )

    selected_relation = relations.relations[0]
    selected_chunks: List[Dict[str, str]] = []
    seen_chunk_ids: set[str] = set()

    for class_name in (selected_relation.subject, selected_relation.object):
        class_key = normalize_object_class_name(class_name)
        relevant_refs = chunk_map.get(class_key, [])
        if not relevant_refs:
            logger.warning("[Codegen:Relation] No relevant chunks found for object class %s", class_name)
            continue

        for chunk in relevant_refs:
            chunk_id = str(chunk.get("chunkId") or chunk.get("chunk_id") or "")
            doc_id = str(chunk.get("docId") or chunk.get("doc_id") or "")
            if not chunk_id or not doc_id:
                continue
            if chunk_id in seen_chunk_ids:
                continue
            seen_chunk_ids.add(chunk_id)
            selected_chunks.append({"doc_id": doc_id, "chunk_id": chunk_id})

    return selected_chunks


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
    key_endpoints = f"{object_class}EndpointsOutput"
    key_attributes = f"{object_class}AttributesOutput"
    async with async_session_maker() as db:
        repo = RelevantChunkRepository(db)
        relevant_map = await repo.get_relevant_chunks_map(session_id, result_keys=[key_endpoints, key_attributes])

    endpoint_refs = relevant_map.get(key_endpoints, [])
    attribute_refs = relevant_map.get(key_attributes, [])

    pairs_endpoints = _collect_pairs(endpoint_refs)
    pairs_attributes = _collect_pairs(attribute_refs)
    merged_pairs = _merge_unique_pairs(pairs_endpoints, pairs_attributes)

    if not merged_pairs:
        return None, None

    chunk_to_doc: Dict[str, str] = {}
    for chunk in [*endpoint_refs, *attribute_refs]:
        if not isinstance(chunk, dict):
            continue
        chunk_id = chunk.get("chunk_id") or chunk.get("chunkId")
        doc_id = chunk.get("doc_id") or chunk.get("docId")
        if isinstance(chunk_id, str) and isinstance(doc_id, str) and chunk_id not in chunk_to_doc:
            chunk_to_doc[chunk_id] = doc_id

    relevant_indices = [i for i, _ in merged_pairs]
    relevant_pairs = [
        {"chunk_id": chunk_id, "doc_id": chunk_to_doc[chunk_id]} if chunk_id in chunk_to_doc else {"chunk_id": chunk_id}
        for _, chunk_id in merged_pairs
        if chunk_id
    ]

    logger.info(
        "[Codegen:%s] Relevant chunks for endpoints=%d, for attributes=%d, merged=%d for %s",
        operation_name,
        len(pairs_endpoints),
        len(pairs_attributes),
        len(merged_pairs),
        object_class,
    )

    return relevant_indices, relevant_pairs


async def _collect_authorization_relevant_chunks(
    session_id: UUID,
    auth_payload: AuthPayload,
    preferred_authorizations: Optional[List[Dict[str, Any]]],
) -> Tuple[Optional[List[int]], Optional[List[Dict[str, Any]]]]:
    async with async_session_maker() as db:
        repo = RelevantChunkRepository(db)
        relevant_map = await repo.get_relevant_chunks_map(session_id, result_keys=["authOutput"])

    auth_pairs = select_authorization_chunk_refs(relevant_map, auth_payload, preferred_authorizations)
    if not auth_pairs:
        if preferred_authorizations and not has_matching_preferred_authorization(
            auth_payload, preferred_authorizations
        ):
            logger.info("[Codegen:Authorization] No selected authorization was identified in analyzed auth output")
            return [], []
        return None, None

    relevant_indices = list(range(len(auth_pairs)))

    logger.info(
        "[Codegen:Authorization] Relevant auth chunks selected=%d preferred=%s",
        len(auth_pairs),
        bool(preferred_authorizations),
    )
    return relevant_indices, auth_pairs


async def generate_native_schema_code(
    attributes_payload: AttributesPayload,
    object_class: str,
    *,
    session_id: UUID,
    job_id: UUID,
    protocol: ApiType,
    repair_context: Optional[CodegenRepairContext] = None,
) -> Dict[str, str]:
    """
    Generate Groovy for native schema mapping from attributes.
    """

    assets = get_operation_assets("native_schema", protocol)
    docs_text = load_required_adoc_text(__package__ + ".documentations", assets.docs_path)

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
        repair_context=repair_context,
    )
    return {"code": code}


async def generate_authorization_code(
    *,
    auth_payload: AuthPayload,
    preferred_authorizations: Optional[List[Dict[str, Any]]] = None,
    session_id: UUID,
    job_id: UUID,
    protocol: ApiType,
    repair_context: Optional[CodegenRepairContext] = None,
) -> Dict[str, str]:
    """
    Generate connector-level Groovy for authentication/authorization configuration.
    """
    preferred_authorizations = enrich_preferred_authorizations(auth_payload, preferred_authorizations)

    if is_single_other_authorization(preferred_authorizations):
        logger.info(
            "[Codegen:Authorization:%s] Returning static scaffold for custom 'other' authorization",
            protocol.value,
        )
        return {"code": build_other_authorization_scaffold(protocol)}

    assets = get_operation_assets("authorization", protocol)
    docs_text = load_required_adoc_text(__package__ + ".documentations", assets.docs_path)
    base_api_url = await get_session_base_api_url(session_id, protocol=protocol)

    generator_preferred_authorizations = prepare_preferred_authorizations_for_generation(
        auth_payload,
        preferred_authorizations,
    )

    generator = AuthorizationGenerator(
        preferred_authorizations=generator_preferred_authorizations,
        docs_text=docs_text,
        system_prompt=assets.system_prompt,
        user_prompt=assets.user_prompt,
        protocol=protocol,
        base_api_url=base_api_url,
    )

    relevant_indices, relevant_pairs = await _collect_authorization_relevant_chunks(
        session_id,
        auth_payload,
        preferred_authorizations,
    )

    code = await generator.generate(
        session_id=session_id,
        relevant_chunk_indices=relevant_indices,
        relevant_chunk_pairs=relevant_pairs,
        job_id=job_id,
        repair_context=repair_context,
        auth_payload=auth_payload,
    )
    return {"code": code}


async def generate_conn_id_code(
    attributes_payload: AttributesPayload,
    object_class: str,
    *,
    job_id: UUID,
    repair_context: Optional[CodegenRepairContext] = None,
) -> Dict[str, str]:
    """
    Generate Groovy for ConnID attribute mapping from attributes.
    """
    docs_text = load_required_adoc_text(
        __package__ + ".documentations" + ".rest", "30-attribute-to-connid-attributes.adoc"
    )

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
        repair_context=repair_context,
    )
    return {"code": code}


async def generate_search_code(
    *,
    attributes: AttributesPayload,
    endpoints: Optional[EndpointsPayload] = None,
    preferred_endpoints: Optional[List[Dict[str, Any]]] = None,
    session_id: UUID,
    object_class: str,
    intent: SearchIntent,
    job_id: UUID,
    protocol: ApiType,
    repair_context: Optional[CodegenRepairContext] = None,
) -> Dict[str, str]:
    """
    Generate the Groovy `search {}` block using relevant chunks + docs.
    Uses the protocol-specific prompts and documentation for the resolved api_type.
    """
    assets = get_search_operation_assets(protocol, intent)
    docs_text = load_required_adoc_text(__package__ + ".documentations", assets.docs_path)
    base_api_url, database_name = await get_session_connection_target(session_id, protocol=protocol)

    generator = SearchGenerator(
        object_class=object_class,
        intent=intent,
        preferred_endpoints=preferred_endpoints,
        docs_text=docs_text,
        system_prompt=assets.system_prompt,
        user_prompt=assets.user_prompt,
        protocol_label=protocol.value,
        base_api_url=base_api_url,
        database_name=database_name,
    )

    # Collect relevant chunks
    relevant_indices, relevant_pairs = await _collect_relevant_chunks(session_id, object_class, "Search")

    # Generate code
    code = await generator.generate(
        session_id=session_id,
        relevant_chunk_indices=relevant_indices,
        relevant_chunk_pairs=relevant_pairs,
        job_id=job_id,
        repair_context=repair_context,
        attributes=attributes,
        endpoints=endpoints,
    )
    return {"code": code}


async def generate_create_code(
    *,
    attributes: AttributesPayload,
    endpoints: Optional[EndpointsPayload] = None,
    preferred_endpoints: Optional[List[Dict[str, Any]]] = None,
    session_id: UUID,
    object_class: str,
    job_id: UUID,
    protocol: ApiType,
    repair_context: Optional[CodegenRepairContext] = None,
) -> Dict[str, str]:
    """
    Generate the Groovy `create {}` block using relevant chunks + docs.
    Uses the protocol-specific prompts and documentation for the resolved api_type.
    """
    assets = get_operation_assets("create", protocol)
    docs_text = load_required_adoc_text(__package__ + ".documentations", assets.docs_path)
    base_api_url, database_name = await get_session_connection_target(session_id, protocol=protocol)

    generator = CreateGenerator(
        object_class=object_class,
        preferred_endpoints=preferred_endpoints,
        docs_text=docs_text,
        system_prompt=assets.system_prompt,
        user_prompt=assets.user_prompt,
        protocol_label=protocol.value,
        base_api_url=base_api_url,
        database_name=database_name,
    )

    # Collect relevant chunks
    relevant_indices, relevant_pairs = await _collect_relevant_chunks(session_id, object_class, "Create")

    # Generate code
    code = await generator.generate(
        session_id=session_id,
        relevant_chunk_indices=relevant_indices,
        relevant_chunk_pairs=relevant_pairs,
        job_id=job_id,
        repair_context=repair_context,
        attributes=attributes,
        endpoints=endpoints,
    )
    return {"code": code}


async def generate_update_code(
    *,
    attributes: AttributesPayload,
    endpoints: Optional[EndpointsPayload] = None,
    preferred_endpoints: Optional[List[Dict[str, Any]]] = None,
    session_id: UUID,
    object_class: str,
    job_id: UUID,
    protocol: ApiType,
    repair_context: Optional[CodegenRepairContext] = None,
) -> Dict[str, str]:
    """
    Generate the Groovy `update {}` block using relevant chunks + docs.
    Uses the protocol-specific prompts and documentation for the resolved api_type.
    """
    assets = get_operation_assets("update", protocol)
    docs_text = load_required_adoc_text(__package__ + ".documentations", assets.docs_path)
    base_api_url, database_name = await get_session_connection_target(session_id, protocol=protocol)

    generator = UpdateGenerator(
        object_class=object_class,
        preferred_endpoints=preferred_endpoints,
        docs_text=docs_text,
        system_prompt=assets.system_prompt,
        user_prompt=assets.user_prompt,
        protocol_label=protocol.value,
        base_api_url=base_api_url,
        database_name=database_name,
    )

    # Collect relevant chunks
    relevant_indices, relevant_pairs = await _collect_relevant_chunks(session_id, object_class, "Update")

    # Generate code
    code = await generator.generate(
        session_id=session_id,
        relevant_chunk_indices=relevant_indices,
        relevant_chunk_pairs=relevant_pairs,
        job_id=job_id,
        repair_context=repair_context,
        attributes=attributes,
        endpoints=endpoints,
    )
    return {"code": code}


async def generate_delete_code(
    *,
    attributes: AttributesPayload,
    endpoints: Optional[EndpointsPayload] = None,
    preferred_endpoints: Optional[List[Dict[str, Any]]] = None,
    session_id: UUID,
    object_class: str,
    job_id: UUID,
    protocol: ApiType,
    repair_context: Optional[CodegenRepairContext] = None,
) -> Dict[str, str]:
    """
    Generate the Groovy `delete {}` block using relevant chunks + docs.
    Uses the protocol-specific prompts and documentation for the resolved api_type.
    """
    assets = get_operation_assets("delete", protocol)
    docs_text = load_required_adoc_text(__package__ + ".documentations", assets.docs_path)
    base_api_url, database_name = await get_session_connection_target(session_id, protocol=protocol)

    generator = DeleteGenerator(
        object_class=object_class,
        preferred_endpoints=preferred_endpoints,
        docs_text=docs_text,
        system_prompt=assets.system_prompt,
        user_prompt=assets.user_prompt,
        protocol_label=protocol.value,
        base_api_url=base_api_url,
        database_name=database_name,
    )

    # Collect relevant chunks
    relevant_indices, relevant_pairs = await _collect_relevant_chunks(session_id, object_class, "Delete")

    # Generate code
    code = await generator.generate(
        session_id=session_id,
        relevant_chunk_indices=relevant_indices,
        relevant_chunk_pairs=relevant_pairs,
        job_id=job_id,
        repair_context=repair_context,
        attributes=attributes,
        endpoints=endpoints,
    )
    return {"code": code}


async def generate_relation_code(
    *,
    relations: RelationsResponse,
    relation_name: str,
    session_id: UUID,
    job_id: UUID,
) -> Dict[str, str]:
    """
    Generate the Groovy `relation {}` block using relevant chunks + docs.
    """
    relation_docs_text = load_required_adoc_text(__package__ + ".documentations" + ".rest", "50-relationship.adoc")

    relevant_indices: Optional[List[int]] = None
    relevant_pairs: Optional[List[Dict[str, Any]]] = None

    object_class_chunks = await _collect_relation_object_class_pairs(relations, session_id)
    relevant_pairs = object_class_chunks
    pairs = _collect_pairs(object_class_chunks)
    if pairs:
        relevant_indices = [i for i, _ in pairs]
        selected_relation = relations.relations[0]
        logger.info(
            "[Codegen:Relation] Relevant chunks from DB for %s: subject=%s, object=%s, chunks=%d",
            relation_name,
            selected_relation.subject,
            selected_relation.object,
            len(relevant_pairs) if relevant_pairs else 0,
        )
    else:
        logger.warning("[Codegen:Relation] No relevant object-class chunks found for relation %s", relation_name)

    generator = RelationGenerator(docs_text=relation_docs_text)
    code = await generator.generate(
        session_id=session_id,
        relevant_chunk_indices=relevant_indices,
        relevant_chunk_pairs=relevant_pairs,
        job_id=job_id,
        relations=relations,
        relation_name=relation_name,
    )
    return {"code": code}
