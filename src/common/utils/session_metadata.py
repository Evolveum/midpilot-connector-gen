# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Shared helpers for working with session metadata."""

import logging
from typing import Any, Dict, Iterable, List, Optional
from uuid import UUID

from ..database.config import async_session_maker
from ..database.repositories.session_repository import SessionRepository

logger = logging.getLogger(__name__)


def _collect_info_metadata(metadata: Dict[str, Any] | None) -> Dict[str, Any]:
    if not metadata or not isinstance(metadata, dict):
        return {}

    info_metadata = metadata.get("infoMetadata") or metadata.get("InfoMetadata")
    if not isinstance(info_metadata, dict):
        return {}

    return info_metadata


def extract_api_type(metadata: Dict[str, Any] | None) -> List[str]:
    """Return the apiType list from metadata payload (case-sensitive key)."""
    info_metadata = _collect_info_metadata(metadata)
    api_type = info_metadata.get("apiType", [])
    return api_type if isinstance(api_type, list) else []


def extract_base_api_url(metadata: Dict[str, Any] | None) -> str:
    """Return the first documented baseApiEndpoint uri, if present."""
    info_metadata = _collect_info_metadata(metadata)
    base_api_endpoints = info_metadata.get("baseApiEndpoint", [])
    if base_api_endpoints and isinstance(base_api_endpoints, list):
        first = base_api_endpoints[0]
        if isinstance(first, dict):
            uri = first.get("uri")
            if isinstance(uri, str):
                return uri
    return ""


def is_scim_api(api_types: Iterable[str]) -> bool:
    """Detect SCIM when it appears anywhere in the API type list (case-insensitive)."""
    return any(isinstance(api, str) and api.strip().upper() == "SCIM" for api in api_types)


async def load_session_metadata(session_id: UUID, key: str = "metadataOutput") -> Optional[Dict[str, Any]]:
    """Load and validate metadata stored under a session."""
    try:
        async with async_session_maker() as db:
            repo = SessionRepository(db)
            metadata = await repo.get_session_data(session_id, key)
            if metadata and isinstance(metadata, dict):
                return metadata
            return None
    except Exception as exc:
        logger.warning("[SessionMetadataUtils] Failed to load %s for %s: %s", key, session_id, exc)
        return None


async def get_session_api_types(session_id: UUID) -> List[str]:
    """Return the normalized apiType list for a session."""
    metadata = await load_session_metadata(session_id)
    return extract_api_type(metadata)


async def get_session_base_api_url(session_id: UUID) -> str:
    """Return the base API URL documented in session metadata, if any."""
    metadata = await load_session_metadata(session_id)
    return extract_base_api_url(metadata)
