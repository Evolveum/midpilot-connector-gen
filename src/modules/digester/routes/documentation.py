# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""HTTP adapter for the wizard's source documentation panel."""

from uuid import UUID

from fastapi import APIRouter, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import config
from src.core.db import DbSession
from src.database.repositories.session_repository import SessionRepository
from src.modules.digester.documentation import get_relevant_documentation
from src.modules.digester.schemas.common import NormalizedEndpointMethod
from src.modules.digester.schemas.documentation import RelevantDocumentationResponse
from src.session.access import ensure_session_exists

router = APIRouter(tags=["Digester: Documentation"])


@router.get(
    "/{session_id}/classes/{object_class}/documentation",
    response_model=RelevantDocumentationResponse,
    summary="Get source documentation for an object class or endpoint",
    responses={404: {"description": "Session, class or endpoint unavailable"}},
)
async def get_class_documentation(
    session_id: UUID,
    object_class: str = Path(..., min_length=1),
    method: NormalizedEndpointMethod | None = Query(None, description="External API HTTP method; requires path."),
    path: str | None = Query(None, min_length=1, description="Exact external API path template; requires method."),
    offset: int = Query(0, ge=0),
    limit: int = Query(config.digester.documentation_page_size, ge=1, le=config.digester.documentation_max_page_size),
    db: AsyncSession = DbSession,
) -> RelevantDocumentationResponse:
    """Return whole stored source chunks. Omit method and path for class-level sources.

    References identify provenance, not a guarantee of correctness. No extraction
    or LLM calls are performed. Missing references produce an empty items array.
    """
    repo = SessionRepository(db)
    await ensure_session_exists(repo, session_id)
    return await get_relevant_documentation(
        db,
        repo,
        session_id,
        object_class,
        method=method,
        path=path,
        offset=offset,
        limit=limit,
    )
