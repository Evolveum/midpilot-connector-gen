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


def extract_base_api_url(metadata: Mapping[str, Any] | None) -> str:
    """Return the first documented baseApiEndpoint uri, if present."""
    base_api_endpoints = _collect_info_metadata(metadata).get("baseApiEndpoint", [])
    if not isinstance(base_api_endpoints, list) or not base_api_endpoints:
        return ""

    first = base_api_endpoints[0]
    if isinstance(first, Mapping):
        uri = first.get("uri")
        if isinstance(uri, str):
            return uri
    return ""


def is_scim_api(api_types: Iterable[str]) -> bool:
    """Detect SCIM when it appears anywhere in the API type list (case-insensitive)."""
    return any(isinstance(api, str) and api.strip().upper() == "SCIM" for api in api_types)


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


async def get_session_base_api_url(session_id: UUID) -> str:
    """Return the base API URL documented in session metadata, if any."""
    metadata = await load_session_metadata(session_id)
    return extract_base_api_url(metadata)
