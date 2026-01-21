#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

"""
Utility to fetch and check API type from session metadata.
Used to determine whether to use SCIM-specific prompts or REST/OpenAPI prompts.
"""

import logging
from typing import List
from uuid import UUID

from ....common.database.config import async_session_maker
from ....common.database.repositories.session_repository import SessionRepository

logger = logging.getLogger(__name__)


async def get_api_types_from_session(session_id: UUID) -> List[str]:
    """
    Fetch api_type list from session metadata.

    Args:
        session_id: The session UUID

    Returns:
        List of API types (e.g., ["REST", "OpenAPI"] or ["SCIM"])
        Returns empty list if metadata not found
    """
    try:
        async with async_session_maker() as db:
            repo = SessionRepository(db)
            metadata = await repo.get_session_data(session_id, "metadataOutput")

            if not metadata:
                logger.debug("[APITypeHelper] No metadata found in session %s", session_id)
                return []

            info_about_schema = metadata.get("infoAboutSchema", {})
            api_types = info_about_schema.get("apiType", [])

            logger.info("[APITypeHelper] Detected api_types for session %s: %s", session_id, api_types)
            return api_types

    except Exception as e:
        logger.warning("[APITypeHelper] Failed to fetch api_type from session %s: %s", session_id, e)
        return []


def is_scim_api(api_types: List[str]) -> bool:
    """
    Check if the API type list contains SCIM.

    Args:
        api_types: List of API types from metadata

    Returns:
        True if "SCIM" is in the list
    """
    return "SCIM" in api_types
