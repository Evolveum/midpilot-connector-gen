# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from ....common.database.config import async_session_maker
from ....common.database.repositories.session_repository import SessionRepository

logger = logging.getLogger(__name__)


def extract_api_type(metadata: Dict[str, Any] | None) -> List[str]:
    """Extract apiType list from metadataOutput payload."""
    if not metadata:
        return []

    info_metadata = metadata.get("infoMetadata") or {}
    api_type = info_metadata.get("apiType", [])
    return api_type if isinstance(api_type, list) else []


def extract_base_api_url(metadata: Dict[str, Any] | None) -> str:
    """Extract first base API URL from metadataOutput payload."""
    if not metadata:
        return ""

    info_metadata = metadata.get("infoMetadata") or {}
    base_api_endpoints = info_metadata.get("baseApiEndpoint", [])
    if base_api_endpoints and isinstance(base_api_endpoints, list):
        first = base_api_endpoints[0]
        if isinstance(first, dict):
            uri = first.get("uri")
            if isinstance(uri, str):
                return uri
    return ""


def is_scim_api(api_type: List[str]) -> bool:
    """Return True when SCIM is present in API type list (case-insensitive)."""
    return any(isinstance(api, str) and api.strip().upper() == "SCIM" for api in api_type)


def protocol_selection_message(
    scope: str,
    *,
    is_scim: bool,
    scim_mode: str,
    rest_mode: str,
    object_class: Optional[str] = None,
) -> str:
    """Build unified protocol selection log message."""
    protocol = "SCIM" if is_scim else "REST"
    selected_mode = scim_mode if is_scim else rest_mode
    target = f" for {object_class}" if object_class else ""
    return f"[{scope}] {protocol} detected{target}, using {selected_mode}"


async def load_session_api_context(repo: SessionRepository, session_id: UUID) -> Tuple[List[str], str]:
    """Load apiType and baseApiUrl from session metadataOutput."""
    metadata = await repo.get_session_data(session_id, "metadataOutput")
    if not metadata or not isinstance(metadata, dict):
        return [], ""
    return extract_api_type(metadata), extract_base_api_url(metadata)


async def get_api_type_from_session(session_id: UUID) -> List[str]:
    """
    Retrieve apiType from metadataOutput stored in session.

    Returns empty list on missing metadata or failures.
    """
    try:
        async with async_session_maker() as db:
            repo = SessionRepository(db)
            metadata = await repo.get_session_data(session_id, "metadataOutput")
            api_type = extract_api_type(metadata if isinstance(metadata, dict) else None)

            if not api_type:
                logger.info("[Digester:Service] No metadataOutput/apiType found, defaulting to REST")
            else:
                logger.info("[Digester:Service] Detected API type from session: %s", api_type)

            return api_type

    except Exception as e:
        logger.warning("[Digester:Service] Failed to retrieve api_type from session: %s, defaulting to REST", e)
        return []
