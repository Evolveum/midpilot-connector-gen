# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Shared helpers for working with session metadata."""

import logging
from collections.abc import Iterable, Mapping
from typing import Any
from uuid import UUID

from src.common.database.config import async_session_maker
from src.common.database.repositories.session_repository import SessionRepository
from src.common.enums import ApiType

logger = logging.getLogger(__name__)


def _collect_info_metadata(metadata: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(metadata, Mapping):
        return {}

    info_metadata = metadata.get("infoMetadata") or metadata.get("InfoMetadata")
    if not isinstance(info_metadata, Mapping):
        return {}

    return info_metadata


def extract_api_type(metadata: Mapping[str, Any] | None) -> list[str]:
    """Return the apiType list from metadata payload (case-sensitive key)."""
    api_type = _collect_info_metadata(metadata).get("apiType", [])
    return api_type if isinstance(api_type, list) else []


def _first_endpoint_uri(block: Any) -> str:
    """Return the first base endpoint uri inside an availability block, if present."""
    if not isinstance(block, Mapping):
        return ""
    endpoints = block.get("baseApiEndpoint", [])
    if not isinstance(endpoints, list) or not endpoints:
        return ""

    first = endpoints[0]
    if isinstance(first, Mapping):
        uri = first.get("uri")
        if isinstance(uri, str):
            return uri
    return ""


_PROTOCOL_AVAILABILITY_BLOCK: dict[ApiType, str] = {
    ApiType.REST: "restAvailability",
    ApiType.SCIM: "scimAvailability",
}


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

    api_types = info.get("apiType", [])
    api_types = api_types if isinstance(api_types, list) else []
    block_order = (
        ("scimAvailability", "restAvailability")
        if resolve_session_api_type(api_types) is ApiType.SCIM
        else ("restAvailability", "scimAvailability")
    )
    for block_key in block_order:
        uri = _first_endpoint_uri(info.get(block_key))
        if uri:
            return uri
    return ""


def extract_database_name(metadata: Mapping[str, Any] | None) -> str:
    """Return the documented databaseName, if present (SQL integrations only)."""
    sql_block = _collect_info_metadata(metadata).get("sqlAvailability", {})
    if not isinstance(sql_block, Mapping):
        return ""
    database_name = sql_block.get("databaseName", "")
    return database_name if isinstance(database_name, str) else ""


def is_scim_api(api_types: Iterable[str]) -> bool:
    """Detect SCIM when it appears anywhere in the API type list."""
    return any(isinstance(api, str) and api.strip().lower() == ApiType.SCIM.value for api in api_types)


def is_sql_api(api_types: Iterable[str]) -> bool:
    """Detect SQL when it appears anywhere in the API type list."""
    return any(isinstance(api, str) and api.strip().lower() == ApiType.SQL.value for api in api_types)


def resolve_session_api_type(api_types: Iterable[str]) -> ApiType:
    """Resolve session apiType metadata to the codegen protocol, defaulting to REST."""
    normalized = {api.strip().lower() for api in api_types if isinstance(api, str)}
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
    metadata = await load_session_metadata(session_id)
    return extract_api_type(metadata)


async def get_session_base_api_url(session_id: UUID, protocol: ApiType | None = None) -> str:
    """Return the base API URL documented in session metadata, if any.

    Pass ``protocol`` (e.g. the effective/overridden apiType) to read that protocol's base URL
    strictly; omit it to derive from the stored apiType. See ``extract_base_api_url``.
    """
    metadata = await load_session_metadata(session_id)
    return extract_base_api_url(metadata, protocol)


async def get_session_database_name(session_id: UUID) -> str:
    """Return the database name documented in session metadata, if any (SQL integrations)."""
    metadata = await load_session_metadata(session_id)
    return extract_database_name(metadata)


async def get_discovery_application_name(session_id: UUID) -> str:
    """Return the application name the user entered in discovery, if any."""
    discovery_input = await load_session_metadata(session_id, key="discoveryInput")
    if not isinstance(discovery_input, dict):
        return ""
    name = discovery_input.get("applicationName")
    return name.strip() if isinstance(name, str) else ""


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
