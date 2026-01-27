# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any, Dict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ....common.chunk_filter.filter import filter_documentation_items
from .criteria import AUTH_CRITERIA, DEFAULT_CRITERIA


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
    total_length = sum(len(item["content"]) for item in doc_items)
    return {
        "sessionInput": {
            "documentationItemsCount": len(doc_items),
            "totalLength": total_length,
        },
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
    # Apply static category filter to documentation items
    doc_items = await filter_documentation_items(AUTH_CRITERIA, session_id, db=db)
    total_length = sum(len(item["content"]) for item in doc_items)
    return {
        "sessionInput": {
            "documentationItemsCount": len(doc_items),
            "totalLength": total_length,
        },
        "jobInput": {
            "documentationItems": doc_items,
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
    total_length = sum(len(item["content"]) for item in doc_items)
    return {
        "sessionInput": {
            "documentationItemsCount": len(doc_items),
            "totalLength": total_length,
        },
        "jobInput": {
            "documentationItems": doc_items,
        },
        "args": (doc_items,),
    }
