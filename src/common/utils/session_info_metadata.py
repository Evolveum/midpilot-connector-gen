# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Shared helpers for working with session metadata."""

import logging
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from src.common.database.config import async_session_maker
from src.common.database.repositories.session_repository import SessionRepository
from src.common.enums import ApiType
from src.common.utils.coerce import as_list, as_mapping, as_str, as_str_list

logger = logging.getLogger(__name__)

# Maps a protocol to the availability block that carries its base endpoint. The single source
# of truth for protocol <-> block: both the strict read and the no-protocol fallback derive
# from it, so the read side cannot drift from the write side in the digester merge.
_PROTOCOL_AVAILABILITY_BLOCK: dict[ApiType, str] = {
    ApiType.REST: "restAvailability",
    ApiType.SCIM: "scimAvailability",
}


def _collect_info_metadata(metadata: Mapping[str, Any] | None) -> Mapping[str, Any]:
    root = as_mapping(metadata)
    return as_mapping(root.get("infoMetadata") or root.get("InfoMetadata"))


def _normalize_api_types(api_types: Any) -> set[str]:
    """Lower-cased set of the string apiType values in a payload (non-strings are ignored)."""
    return {api_type.strip().lower() for api_type in as_str_list(api_types)}


def extract_api_type(metadata: Mapping[str, Any] | None) -> list[str]:
    """Return the apiType list from metadata payload (case-sensitive key)."""
    return as_str_list(_collect_info_metadata(metadata).get("apiType"))


def _first_endpoint_uri(block: Any) -> str:
    """Return the first base endpoint uri inside an availability block, if present."""
    endpoints = as_list(as_mapping(block).get("baseApiEndpoint"))
    if not endpoints:
        return ""
    return as_str(as_mapping(endpoints[0]).get("uri"))


def extract_base_api_url(metadata: Mapping[str, Any] | None, protocol: ApiType | None = None) -> str:
    """
    Return the documented HTTP base endpoint uri for the connector, if present.

    REST and SCIM endpoints live in separate availability blocks.

    - When ``protocol`` is given (e.g. an explicit ``apiType`` override, or a protocol-specific
      codegen run), it is honored strictly: only that protocol's block is read, never another
      protocol's base URL. SQL (and any non-HTTP protocol) has no base endpoint, so this
      yields an empty string.
    - When ``protocol`` is ``None``, the protocol is derived from the stored ``apiType`` and the
      other HTTP block is used as a fallback, so callers without a protocol directive still get
      a documented base URL when endpoint classification is imperfect.
    """
    info = _collect_info_metadata(metadata)

    if protocol is not None:
        block_key = _PROTOCOL_AVAILABILITY_BLOCK.get(protocol)
        return _first_endpoint_uri(info.get(block_key)) if block_key else ""

    preferred_block = _PROTOCOL_AVAILABILITY_BLOCK.get(resolve_session_api_type(info.get("apiType")))
    candidate_blocks = [preferred_block, *_PROTOCOL_AVAILABILITY_BLOCK.values()]
    for block_key in dict.fromkeys(block for block in candidate_blocks if block):
        if uri := _first_endpoint_uri(info.get(block_key)):
            return uri
    return ""


def extract_database_name(metadata: Mapping[str, Any] | None) -> str:
    """Return the documented databaseName, if present (SQL integrations only)."""
    sql_block = as_mapping(_collect_info_metadata(metadata).get("sqlAvailability"))
    return as_str(sql_block.get("databaseName"))


def is_scim_api(api_types: Any) -> bool:
    """Detect SCIM when it appears anywhere in the API type list."""
    return ApiType.SCIM.value in _normalize_api_types(api_types)


def is_sql_api(api_types: Any) -> bool:
    """Detect SQL when it appears anywhere in the API type list."""
    return ApiType.SQL.value in _normalize_api_types(api_types)


def resolve_session_api_type(api_types: Any) -> ApiType:
    """Resolve session apiType metadata to the codegen protocol, defaulting to REST."""
    normalized = _normalize_api_types(api_types)
    if ApiType.SQL.value in normalized:
        return ApiType.SQL
    if ApiType.SCIM.value in normalized:
        return ApiType.SCIM
    return ApiType.REST


async def resolve_effective_api_type(session_id: UUID, override: ApiType | None) -> ApiType:
    """
    Resolve the protocol to use for an operation.

    When the caller provides an explicit ``override`` (e.g. the ``apiType`` request
    parameter) it wins unconditionally. Otherwise the protocol is derived from the
    apiType the LLM stored in the session ``infoMetadata``.
    """
    if override is not None:
        return override
    return resolve_session_api_type(await get_session_api_types(session_id))


async def load_session_metadata(session_id: UUID, key: str = "metadataOutput") -> dict[str, Any] | None:
    """Load and validate metadata stored under a session."""
    try:
        async with async_session_maker() as db:
            repo = SessionRepository(db)
            metadata = await repo.get_session_data(session_id, key)
    except Exception as exc:
        logger.warning("[SessionMetadataUtils] Failed to load %s for %s: %s", key, session_id, exc)
        return None
    return metadata if isinstance(metadata, dict) else None


async def get_session_api_types(session_id: UUID) -> list[str]:
    """Return the normalized apiType list for a session."""
    return extract_api_type(await load_session_metadata(session_id))


async def get_session_base_api_url(session_id: UUID, protocol: ApiType | None = None) -> str:
    """Return the base API URL documented in session metadata, if any.

    Pass ``protocol`` (e.g. the effective/overridden apiType) to read that protocol's base URL
    strictly; omit it to derive from the stored apiType. See ``extract_base_api_url``.
    """
    return extract_base_api_url(await load_session_metadata(session_id), protocol)


async def get_discovery_application_name(session_id: UUID) -> str:
    """Return the application name the user entered in discovery, if any."""
    discovery_input = await load_session_metadata(session_id, key="discoveryInput")
    return as_str(as_mapping(discovery_input).get("applicationName")).strip()


async def get_session_connection_target(session_id: UUID, protocol: ApiType | None = None) -> tuple[str, str]:
    """
    Return the connector's connection target from a single session metadata load.

    Yields (base_api_url, database_name). Only one is populated for a given session
    (HTTP base URL for REST/SCIM, database name for SQL); the other is an empty string.

    Pass ``protocol`` (e.g. the effective/overridden apiType) so the HTTP base URL is read
    strictly from that protocol's block; omit it to derive from the stored apiType.
    """
    metadata = await load_session_metadata(session_id)
    return extract_base_api_url(metadata, protocol), extract_database_name(metadata)
