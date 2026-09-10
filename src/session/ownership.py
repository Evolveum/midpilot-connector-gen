# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Centralized session-ownership enforcement.

Registered in ``src.app`` as a router-level dependency right after
``src.auth.dependencies.authenticate_request``. Every session route uses the
uniform ``session_id`` path parameter, so the check reads it from
``request.path_params`` - handlers keep their ``ensure_session_exists`` calls
as pure existence checks and no route needs individual wiring.
"""

import logging
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.context import AuthContext, AuthMode
from src.core.db import DbSession
from src.database.repositories.session_repository import SessionRepository
from src.session.errors import SessionNotFoundError

logger = logging.getLogger(__name__)


async def enforce_session_ownership(request: Request, db: AsyncSession = DbSession) -> None:
    """Reject access to a session owned by a different API key.

    A foreign or ownerless session is masked as 404 (anti-enumeration); the
    real reason is logged with the key ID. A nonexistent session passes
    through so handlers can decide between 404 and creation
    (``POST /session/{session_id}``).
    """
    context: AuthContext | None = getattr(request.state, "auth", None)
    if context is None:
        raise RuntimeError("AuthContext missing on request state; authenticate_request must run before this check")

    if context.mode is not AuthMode.api_key:
        return

    raw_session_id = request.path_params.get("session_id")
    if raw_session_id is None:
        return

    try:
        session_id = UUID(str(raw_session_id))
    except ValueError:
        # Malformed session_id: let FastAPI path validation produce its 422.
        return

    owner = await SessionRepository(db).get_session_owner(session_id)
    if owner is None:
        return

    if not context.can_access_session(owner.api_key_id):
        logger.warning(
            "API key %s denied access to session %s (owner: %s); responding 404",
            context.api_key_id,
            session_id,
            owner.api_key_id or "none",
        )
        raise SessionNotFoundError(session_id)
