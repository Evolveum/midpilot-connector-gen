# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""FastAPI dependencies enforcing API key authentication and session ownership.

``authenticate_request`` is registered once as a router-level dependency in
``src.app`` and therefore runs for every API route. Besides authenticating the
key it also performs the session ownership check centrally: every session
route uses the uniform ``session_id`` path parameter, so the dependency reads
it from ``request.path_params`` and rejects access to sessions owned by a
different API key. Handlers keep their existing ``ensure_session_exists``
calls, which remain pure existence checks.
"""

import logging
from typing import Optional
from uuid import UUID

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth.context import AuthContext, AuthMode
from src.common.auth.keys import KEY_PREFIX_LENGTH, hash_api_key, matches_master_key
from src.common.database.config import get_db
from src.common.database.repositories.api_key_repository import ApiKeyRepository
from src.common.database.repositories.session_repository import SessionRepository
from src.common.errors import (
    InvalidApiKeyError,
    MasterKeyRequiredError,
    MissingApiKeyError,
    SessionNotFoundError,
)
from src.config import config

logger = logging.getLogger(__name__)

API_KEY_HEADER_NAME = "X-API-Key"

api_key_header = APIKeyHeader(
    name=API_KEY_HEADER_NAME,
    auto_error=False,
    description="API key issued by this service (or the master key).",
)


def _configured_master_key() -> Optional[str]:
    master = config.auth.master_api_key
    value = master.get_secret_value() if master else None
    return value or None


async def _resolve_context(api_key: Optional[str], db: AsyncSession) -> AuthContext:
    if not config.auth.api_key_required:
        return AuthContext(mode=AuthMode.disabled)

    if not api_key:
        raise MissingApiKeyError()

    master_key = _configured_master_key()
    if master_key and matches_master_key(api_key, master_key):
        return AuthContext(mode=AuthMode.master)

    record = await ApiKeyRepository(db).get_active_key_by_hash(hash_api_key(api_key))
    if record is None:
        logger.warning("Rejected unknown or revoked API key (prefix %s)", api_key[:KEY_PREFIX_LENGTH])
        raise InvalidApiKeyError()

    return AuthContext(mode=AuthMode.api_key, api_key_id=record.api_key_id)


async def _enforce_session_ownership(request: Request, context: AuthContext, db: AsyncSession) -> None:
    """Reject access to a session owned by a different API key.

    A foreign or ownerless session is masked as 404 (anti-enumeration); the
    real reason is logged with the key ID. A nonexistent session passes
    through so handlers can decide between 404 and creation
    (``POST /session/{session_id}``).
    """
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


async def authenticate_request(
    request: Request,
    api_key: Optional[str] = Security(api_key_header),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    """Authenticate the request and enforce session ownership.

    Registered as a router-level dependency for all API routes. Stores the
    resulting :class:`AuthContext` on ``request.state.auth`` for handlers that
    need it (session creation records the owning key).
    """
    context = await _resolve_context(api_key, db)
    await _enforce_session_ownership(request, context, db)
    request.state.auth = context
    return context


def get_auth_context(request: Request) -> AuthContext:
    """Return the AuthContext stored by ``authenticate_request``.

    Intended as an endpoint dependency; it must only be used on routes below
    the router that registers ``authenticate_request``.
    """
    context = getattr(request.state, "auth", None)
    if context is None:
        raise RuntimeError("AuthContext missing on request state; authenticate_request did not run for this route")
    return context


async def require_master_key(api_key: Optional[str] = Security(api_key_header)) -> None:
    """Guard for API key management endpoints: master key only.

    Enforced regardless of AUTH__API_KEY_REQUIRED so keys can be provisioned
    before enforcement is switched on. Without a configured master key,
    management is unavailable.
    """
    master_key = _configured_master_key()
    if not master_key:
        raise MasterKeyRequiredError("API key management is unavailable: no master API key is configured.")
    if not api_key:
        raise MissingApiKeyError()
    if not matches_master_key(api_key, master_key):
        logger.warning("Rejected non-master API key on management endpoint (prefix %s)", api_key[:KEY_PREFIX_LENGTH])
        raise MasterKeyRequiredError()
