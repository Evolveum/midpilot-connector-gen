# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from src.jobs import append_job_error
from src.modules.codegen.core.base import endpoints_to_records
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
from src.modules.codegen.repair import NO_ATTRIBUTES_EXTRACTED
from src.modules.codegen.schema import (
    AttributesPayload,
    AuthPayload,
    CodegenRepairContext,
    ConnectorCodeOutput,
    EndpointsPayload,
)
from src.modules.codegen.selection.authorization import (
    enrich_preferred_authorizations,
    is_single_other_authorization,
    prepare_preferred_authorizations_for_generation,
)
from src.modules.codegen.selection.docs_loader import load_operation_documentation, load_required_adoc_text
from src.modules.codegen.selection.protocol_selectors import (
    get_operation_assets,
    get_search_operation_assets,
)
from src.modules.codegen.selection.relevant_chunks import (
    _collect_authorization_relevant_chunks,
    _collect_object_class_chunks,
    _collect_relation_object_class_pairs,
    _collect_relevant_chunks,
)
from src.modules.codegen.utils.code_output import build_connector_code_output
from src.modules.codegen.utils.object_class_casing import restore_object_class_casing
from src.modules.codegen.utils.prompt_records import (
    build_complete_attribute_mapping_records,
    build_scim_contract_prompt_vars,
    build_scim_flatten_candidates,
    build_sql_context_prompt_vars,
    extract_sql_context,
    has_extracted_attributes,
)
from src.modules.digester.errors import SqlPhysicalSchemaNotFoundError
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


async def _operation_chunk_pairs(
    session_id: UUID,
    object_class: str,
    operation_name: str,
    *,
    attributes: AttributesPayload,
    endpoints: Optional[EndpointsPayload],
    preferred_endpoints: Optional[List[Dict[str, Any]]],
) -> Optional[List[Dict[str, Any]]]:
    """
    Relevant chunks for a CRUD/search operation, from the most to the least specific source.

    1. The chunks the class's attributes and endpoints were extracted from.
    2. Otherwise the chunks the object-class extraction marked relevant for the class, so
       generation still tries with what is known about it.
    3. Otherwise, with no attributes, endpoints or preferred endpoints either, nothing is
       known: ``[]`` makes the generator return its fallback artifact without an LLM call.
       With data but no relevance anywhere (e.g. a manually added class), ``None`` keeps
       reading all documentation.
    """
    attribute_and_endpoint_chunks = await _collect_relevant_chunks(session_id, object_class, operation_name)
    if attribute_and_endpoint_chunks is not None:
        return attribute_and_endpoint_chunks

    object_class_chunks = await _collect_object_class_chunks(session_id, object_class)
    if object_class_chunks:
        logger.info(
            "[Codegen:Generation] No attribute or endpoint chunks for %s; %s uses %d chunks relevant to the class",
            object_class,
            operation_name,
            len(object_class_chunks),
        )
        return object_class_chunks

    has_endpoints = endpoints is not None and bool(endpoints_to_records(endpoints))
    if has_extracted_attributes(attributes) or has_endpoints or preferred_endpoints:
        return None

    logger.warning(
        "[Codegen:Generation] No attributes, endpoints or relevant chunks for %s; %s returns its fallback "
        "without LLM calls",
        object_class,
        operation_name,
    )
    return []


async def generate_native_schema_code(
    attributes_payload: AttributesPayload,
    object_class: str,
    *,
    session_id: UUID,
    job_id: UUID,
    protocol: ApiType,
    repair_context: Optional[CodegenRepairContext] = None,
) -> ConnectorCodeOutput:
    """
    Generate Groovy for the native schema, including the object class's ConnID mapping.

    One script carries both: the attribute definitions written against the protocol's
    schema DSL, and the ConnID built-in mapping. They used to be generated separately
    and had to agree on every native attribute name or the connector was rejected.

    Without attributes (the digester found no relevant documentation) there is nothing to
    define: the job finishes with empty code and an explanatory error, without an LLM call.
    A repair still runs, because it works from the code midPoint supplied.
    """
    if repair_context is None and not has_extracted_attributes(attributes_payload):
        logger.warning(
            "[Codegen:NativeSchema] No attributes for %s; returning no native schema without LLM calls",
            object_class,
        )
        await append_job_error(job_id, NO_ATTRIBUTES_EXTRACTED)
        return await build_connector_code_output("")

    assets = get_operation_assets("native_schema", protocol)
    if assets.connid_docs_path is None:
        raise ValueError(f"Native-schema assets for {protocol.value} are missing their ConnID reference document")

    docs_package = __package__ + ".documentations"
    docs_text, declarative_docs_text = await asyncio.to_thread(load_operation_documentation, assets)
    records = build_complete_attribute_mapping_records(attributes_payload)
    extra_prompt_vars = {
        "protocol_schema_docs": docs_text,
        "declarative_docs": declarative_docs_text,
        "connid_attribute_docs": await asyncio.to_thread(
            load_required_adoc_text, docs_package, assets.connid_docs_path
        ),
    }
    if protocol == ApiType.SCIM:
        extra_prompt_vars.update(build_scim_contract_prompt_vars(attributes_payload))
        extra_prompt_vars["scim_flatten_candidates_json"] = json.dumps(
            build_scim_flatten_candidates(attributes_payload), ensure_ascii=False
        )

    if protocol == ApiType.SQL:
        # Only stored attributes can be stale; a repair without attributes runs with an empty SQL context.
        if has_extracted_attributes(attributes_payload) and not extract_sql_context(attributes_payload).get(
            "physicalTable"
        ):
            raise SqlPhysicalSchemaNotFoundError(object_class, stale_payload=True)
        extra_prompt_vars.update(build_sql_context_prompt_vars(attributes_payload))

    code = await generate_groovy(
        records=records,
        object_class=object_class,
        system_prompt=assets.system_prompt,
        user_prompt=assets.user_prompt,
        run_name="Codegen:NativeSchema",
        operation_name="NativeSchema",
        extra_prompt_vars=extra_prompt_vars,
        job_id=job_id,
        repair_context=repair_context,
    )
    code = await asyncio.to_thread(restore_object_class_casing, code, object_class)
    return await build_connector_code_output(code)


async def generate_authorization_code(
    *,
    auth_payload: AuthPayload,
    preferred_authorizations: Optional[List[Dict[str, Any]]] = None,
    session_id: UUID,
    job_id: UUID,
    protocol: ApiType,
    repair_context: Optional[CodegenRepairContext] = None,
) -> ConnectorCodeOutput:
    """
    Generate connector-level Groovy for authentication/authorization configuration.
    """
    preferred_authorizations = enrich_preferred_authorizations(auth_payload, preferred_authorizations)

    if is_single_other_authorization(preferred_authorizations):
        logger.info(
            "[Codegen:Authorization:%s] Returning static scaffold for custom 'other' authorization",
            protocol.value,
        )
        return await build_connector_code_output(build_other_authorization_scaffold(protocol))

    assets = get_operation_assets("authorization", protocol)
    docs_text, declarative_docs_text = await asyncio.to_thread(load_operation_documentation, assets)
    base_api_url = await get_session_base_api_url(session_id, protocol=protocol)

    generator_preferred_authorizations = prepare_preferred_authorizations_for_generation(
        auth_payload,
        preferred_authorizations,
    )

    generator = AuthorizationGenerator(
        preferred_authorizations=generator_preferred_authorizations,
        docs_text=docs_text,
        declarative_docs_text=declarative_docs_text,
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
    return await build_connector_code_output(code)


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
) -> ConnectorCodeOutput:
    """
    Generate the Groovy `search {}` block using relevant chunks + docs.
    Uses the protocol-specific prompts and documentation for the resolved api_type.
    """
    assets = get_search_operation_assets(protocol, intent)
    docs_text, declarative_docs_text = await asyncio.to_thread(load_operation_documentation, assets)
    base_api_url, database_name = await get_session_connection_target(session_id, protocol=protocol)

    generator = SearchGenerator(
        object_class=object_class,
        intent=intent,
        preferred_endpoints=preferred_endpoints,
        docs_text=docs_text,
        declarative_docs_text=declarative_docs_text,
        system_prompt=assets.system_prompt,
        user_prompt=assets.user_prompt,
        protocol_label=protocol.value.upper(),
        base_api_url=base_api_url,
        database_name=database_name,
        include_scim_context=protocol == ApiType.SCIM,
        context_only_for_conndev=_uses_deterministic_context(protocol),
    )

    # Collect relevant chunks
    relevant_pairs = await _operation_chunk_pairs(
        session_id,
        object_class,
        "Search",
        attributes=attributes,
        endpoints=endpoints,
        preferred_endpoints=preferred_endpoints,
    )

    # Generate code
    code = await generator.generate(
        session_id=session_id,
        relevant_chunk_pairs=relevant_pairs,
        job_id=job_id,
        repair_context=repair_context,
        attributes=attributes,
        endpoints=endpoints,
    )
    return await build_connector_code_output(code)


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
) -> ConnectorCodeOutput:
    """
    Generate the Groovy `create {}` block using relevant chunks + docs.
    Uses the protocol-specific prompts and documentation for the resolved api_type.
    """
    assets = get_operation_assets("create", protocol)
    docs_text, declarative_docs_text = await asyncio.to_thread(load_operation_documentation, assets)
    base_api_url, database_name = await get_session_connection_target(session_id, protocol=protocol)

    generator = CreateGenerator(
        object_class=object_class,
        preferred_endpoints=preferred_endpoints,
        docs_text=docs_text,
        declarative_docs_text=declarative_docs_text,
        system_prompt=assets.system_prompt,
        user_prompt=assets.user_prompt,
        protocol_label=protocol.value.upper(),
        base_api_url=base_api_url,
        database_name=database_name,
        include_scim_context=protocol == ApiType.SCIM,
        context_only_for_conndev=_uses_deterministic_context(protocol),
    )

    # Collect relevant chunks
    relevant_pairs = await _operation_chunk_pairs(
        session_id,
        object_class,
        "Create",
        attributes=attributes,
        endpoints=endpoints,
        preferred_endpoints=preferred_endpoints,
    )

    # Generate code
    code = await generator.generate(
        session_id=session_id,
        relevant_chunk_pairs=relevant_pairs,
        job_id=job_id,
        repair_context=repair_context,
        attributes=attributes,
        endpoints=endpoints,
    )
    return await build_connector_code_output(code)


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
) -> ConnectorCodeOutput:
    """
    Generate the Groovy `update {}` block using relevant chunks + docs.
    Uses the protocol-specific prompts and documentation for the resolved api_type.
    """
    assets = get_operation_assets("update", protocol)
    docs_text, declarative_docs_text = await asyncio.to_thread(load_operation_documentation, assets)
    base_api_url, database_name = await get_session_connection_target(session_id, protocol=protocol)

    generator = UpdateGenerator(
        object_class=object_class,
        preferred_endpoints=preferred_endpoints,
        docs_text=docs_text,
        declarative_docs_text=declarative_docs_text,
        system_prompt=assets.system_prompt,
        user_prompt=assets.user_prompt,
        protocol_label=protocol.value.upper(),
        base_api_url=base_api_url,
        database_name=database_name,
        include_scim_context=protocol == ApiType.SCIM,
        context_only_for_conndev=_uses_deterministic_context(protocol),
    )

    # Collect relevant chunks
    relevant_pairs = await _operation_chunk_pairs(
        session_id,
        object_class,
        "Update",
        attributes=attributes,
        endpoints=endpoints,
        preferred_endpoints=preferred_endpoints,
    )

    # Generate code
    code = await generator.generate(
        session_id=session_id,
        relevant_chunk_pairs=relevant_pairs,
        job_id=job_id,
        repair_context=repair_context,
        attributes=attributes,
        endpoints=endpoints,
    )
    return await build_connector_code_output(code)


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
) -> ConnectorCodeOutput:
    """
    Generate the Groovy `delete {}` block using relevant chunks + docs.
    Uses the protocol-specific prompts and documentation for the resolved api_type.
    """
    assets = get_operation_assets("delete", protocol)
    docs_text, declarative_docs_text = await asyncio.to_thread(load_operation_documentation, assets)
    base_api_url, database_name = await get_session_connection_target(session_id, protocol=protocol)

    generator = DeleteGenerator(
        object_class=object_class,
        preferred_endpoints=preferred_endpoints,
        docs_text=docs_text,
        declarative_docs_text=declarative_docs_text,
        system_prompt=assets.system_prompt,
        user_prompt=assets.user_prompt,
        protocol_label=protocol.value.upper(),
        base_api_url=base_api_url,
        database_name=database_name,
        include_scim_context=protocol == ApiType.SCIM,
        context_only_for_conndev=_uses_deterministic_context(protocol),
    )

    # Collect relevant chunks
    relevant_pairs = await _operation_chunk_pairs(
        session_id,
        object_class,
        "Delete",
        attributes=attributes,
        endpoints=endpoints,
        preferred_endpoints=preferred_endpoints,
    )

    # Generate code
    code = await generator.generate(
        session_id=session_id,
        relevant_chunk_pairs=relevant_pairs,
        job_id=job_id,
        repair_context=repair_context,
        attributes=attributes,
        endpoints=endpoints,
    )
    return await build_connector_code_output(code)


async def generate_relation_code(
    *,
    relations: RelationsResponse,
    relation_name: str,
    session_id: UUID,
    job_id: UUID,
    protocol: ApiType,
) -> ConnectorCodeOutput:
    """
    Generate a relationship artifact using the resolved protocol's expert references.
    """
    assets = get_operation_assets("relationship", protocol)
    relation_docs_text, declarative_docs_text = await asyncio.to_thread(load_operation_documentation, assets)

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

    generator = RelationGenerator(
        relation_name=relation_name,
        protocol=protocol,
        docs_text=relation_docs_text,
        declarative_docs_text=declarative_docs_text,
        system_prompt=assets.system_prompt,
        user_prompt=assets.user_prompt,
    )
    code = await generator.generate(
        session_id=session_id,
        relevant_chunk_pairs=relevant_pairs,
        job_id=job_id,
        relations=relations,
    )
    return await build_connector_code_output(code)
