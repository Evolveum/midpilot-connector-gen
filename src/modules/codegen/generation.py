# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

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
from src.modules.codegen.schema import AttributesPayload, AuthPayload, CodegenRepairContext, EndpointsPayload
from src.modules.codegen.selection.authorization import (
    enrich_preferred_authorizations,
    is_single_other_authorization,
    prepare_preferred_authorizations_for_generation,
)
from src.modules.codegen.selection.docs_loader import load_required_adoc_text
from src.modules.codegen.selection.protocol_selectors import get_operation_assets, get_search_operation_assets
from src.modules.codegen.selection.relevant_chunks import (
    _collect_authorization_relevant_chunks,
    _collect_relation_object_class_pairs,
    _collect_relevant_chunks,
)
from src.modules.codegen.utils.prompt_records import (
    build_attribute_mapping_records,
    build_connid_attribute_mapping_records,
    build_scim_contract_prompt_vars,
    build_sql_attribute_mapping_records,
)
from src.modules.digester.schemas import RelationsResponse
from src.session.info_metadata import (
    get_session_base_api_url,
    get_session_connection_target,
)
from src.shared.enums import ApiType

logger = logging.getLogger(__name__)

_DETERMINISTIC_CONTEXT_PROTOCOLS = frozenset({ApiType.SCIM, ApiType.SQL})


def _uses_deterministic_context(protocol: ApiType) -> bool:
    return protocol in _DETERMINISTIC_CONTEXT_PROTOCOLS


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

    records = (
        build_sql_attribute_mapping_records(attributes_payload)
        if protocol == ApiType.SQL
        else build_attribute_mapping_records(attributes_payload)
    )
    extra_prompt_vars = {"user_schema_docs": docs_text}
    if protocol == ApiType.SCIM:
        extra_prompt_vars.update(build_scim_contract_prompt_vars(attributes_payload))

    code = await generate_groovy(
        records=records,
        object_class=object_class,
        system_prompt=assets.system_prompt,
        user_prompt=assets.user_prompt,
        logger_prefix="NativeSchema",
        extra_prompt_vars=extra_prompt_vars,
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

    relevant_pairs = await _collect_authorization_relevant_chunks(
        session_id,
        auth_payload,
        preferred_authorizations,
    )

    code = await generator.generate(
        session_id=session_id,
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

    records = build_connid_attribute_mapping_records(attributes_payload)

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
        protocol_label=protocol.value.upper(),
        base_api_url=base_api_url,
        database_name=database_name,
        include_scim_context=protocol == ApiType.SCIM,
        context_only_for_conndev=_uses_deterministic_context(protocol),
    )

    # Collect relevant chunks
    relevant_pairs = await _collect_relevant_chunks(session_id, object_class, "Search")

    # Generate code
    code = await generator.generate(
        session_id=session_id,
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
        protocol_label=protocol.value.upper(),
        base_api_url=base_api_url,
        database_name=database_name,
        include_scim_context=protocol == ApiType.SCIM,
        context_only_for_conndev=_uses_deterministic_context(protocol),
    )

    # Collect relevant chunks
    relevant_pairs = await _collect_relevant_chunks(session_id, object_class, "Create")

    # Generate code
    code = await generator.generate(
        session_id=session_id,
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
        protocol_label=protocol.value.upper(),
        base_api_url=base_api_url,
        database_name=database_name,
        include_scim_context=protocol == ApiType.SCIM,
        context_only_for_conndev=_uses_deterministic_context(protocol),
    )

    # Collect relevant chunks
    relevant_pairs = await _collect_relevant_chunks(session_id, object_class, "Update")

    # Generate code
    code = await generator.generate(
        session_id=session_id,
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
        protocol_label=protocol.value.upper(),
        base_api_url=base_api_url,
        database_name=database_name,
        include_scim_context=protocol == ApiType.SCIM,
        context_only_for_conndev=_uses_deterministic_context(protocol),
    )

    # Collect relevant chunks
    relevant_pairs = await _collect_relevant_chunks(session_id, object_class, "Delete")

    # Generate code
    code = await generator.generate(
        session_id=session_id,
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

    relevant_pairs = await _collect_relation_object_class_pairs(relations, session_id)
    if relevant_pairs:
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
        relevant_chunk_pairs=relevant_pairs,
        job_id=job_id,
        relations=relations,
        relation_name=relation_name,
    )
    return {"code": code}
