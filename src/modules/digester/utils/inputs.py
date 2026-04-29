# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Any, Dict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.common.chunk_filter.filter import filter_documentation_items
from src.config import config
from src.modules.digester.utils.criteria import AUTH_CRITERIA, DEFAULT_CRITERIA, EXTENDED_AUTH_CRITERIA

logger = logging.getLogger(__name__)


async def object_classes_input(db: AsyncSession, session_id: UUID) -> Dict[str, Any]:
    """
    Dynamic input provider for object classes extraction job.
    It is important to wait for the documentation to be ready before starting the job.
    input:
        session_id - session ID to retrieve documentation items from
        db - SQLAlchemy AsyncSession
    output:
        dict with:
            sessionInput - dict with documentationItemsCount and totalLength - used for input in session field
            jobInput - dict for job input field
            args - tuple with documentation items
    """
    # Apply static category filter to documentation items
    doc_items = await filter_documentation_items(DEFAULT_CRITERIA, session_id, db=db)
    return {
        "sessionInput": {},
        "jobInput": {
            "documentationItems": doc_items,
        },
        "args": (doc_items,),
    }


async def auth_input(db: AsyncSession, session_id: UUID) -> Dict[str, Any]:
    """
    Dynamic input provider for auth extraction job.
    It is important to wait for the documentation to be ready before starting the job.
    input:
        session_id - session ID to retrieve documentation items from
        db - SQLAlchemy AsyncSession
    output:
        dict with:
            args - tuple of documentation items
            sessionInput - dict with documentationItemsCount and totalLength - used for input in session field
            jobInput - dict for job input field
    """
    # Prefer auth-specific chunks, but broaden the category set when the default
    # auth filter is likely too narrow for the current session metadata.
    doc_items = await filter_documentation_items(AUTH_CRITERIA, session_id, db=db)
    min_doc_items = config.digester.auth_min_documentation_items
    used_auth_criteria = True
    if len(doc_items) < min_doc_items:
        logger.info(
            "[Digester:Auth] AUTH_CRITERIA matched %d documentation items for session %s; "
            "falling back to EXTENDED_AUTH_CRITERIA",
            len(doc_items),
            session_id,
        )
        doc_items = await filter_documentation_items(EXTENDED_AUTH_CRITERIA, session_id, db=db)
        used_auth_criteria = False
    return {
        "sessionInput": {},
        "jobInput": {
            "documentationItems": doc_items,
            "usedAuthCriteria": used_auth_criteria,
        },
        "args": (doc_items,),
    }


async def metadata_input(db: AsyncSession, session_id: UUID) -> Dict[str, Any]:
    """
    Dynamic input provider for metadata extraction job.
    It is important to wait for the documentation to be ready before starting the job.
    input:
        session_id - session ID to retrieve documentation items from
        db - SQLAlchemy AsyncSession
    output:
        dict with:
            'args' key containing tuple of documentation items,
            'sessionInput' key with metadata for input in session field,
            'jobInput' key with metadata for input in job field
    """
    doc_items = await filter_documentation_items(DEFAULT_CRITERIA, session_id, db=db)
    return {
        "sessionInput": {},
        "jobInput": {
            "documentationItems": doc_items,
        },
        "args": (doc_items,),
    }
