# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""FastAPI dependencies for API key authentication.

``authenticate_request`` is registered once as a router-level dependency in
``src.app`` and therefore runs for every API route. It stores the resulting
:class:`AuthContext` on ``request.state.auth``; the session-ownership check
that consumes it lives in ``src.session.ownership`` and is registered right
after this dependency.
"""

import logging
from typing import Optional

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.context import AuthContext, AuthMode
from src.auth.errors import InvalidApiKeyError, MasterKeyRequiredError, MissingApiKeyError
from src.auth.keys import KEY_PREFIX_LENGTH, hash_api_key, matches_master_key
from src.config import config
from src.core.db import get_db
from src.database.repositories.api_key_repository import ApiKeyRepository

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


async def authenticate_request(
    request: Request,
    api_key: Optional[str] = Security(api_key_header),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    """Authenticate the request.

    Registered as a router-level dependency for all API routes. Stores the
    resulting :class:`AuthContext` on ``request.state.auth`` for downstream
    dependencies (session ownership) and handlers (session creation records
    the owning key).
    """
    context = await _resolve_context(api_key, db)
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
