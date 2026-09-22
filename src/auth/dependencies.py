# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Resolve session ownership from keys already authenticated by Gravitee.

This does not validate key issuance, expiry or revocation. Only the gateway may
reach the API in production mode; direct access would bypass authentication.
"""

from fastapi import Request, Security
from fastapi.security import APIKeyHeader

from src.auth.context import AuthContext
from src.auth.errors import InvalidApiKeyError, MissingApiKeyError
from src.auth.keys import hash_api_key
from src.config import config
from src.config.auth import AuthMode

API_KEY_HEADER_NAME = "X-Gravitee-Api-Key"

api_key_header = APIKeyHeader(
    name=API_KEY_HEADER_NAME,
    auto_error=False,
    description="API key validated and forwarded by Gravitee; backend access must be restricted to the gateway.",
)


async def authenticate_request(
    request: Request,
    api_key: str | None = Security(api_key_header),
) -> AuthContext:
    """Require unambiguous gateway forwarding and retain only the key hash."""
    if config.auth.mode is AuthMode.dev:
        context = AuthContext(mode=AuthMode.dev)
    else:
        values = request.headers.getlist(API_KEY_HEADER_NAME)
        if not values:
            raise MissingApiKeyError()
        if len(values) != 1 or not api_key or any(ord(char) < 33 or ord(char) > 126 or char == "," for char in api_key):
            raise InvalidApiKeyError()
        context = AuthContext(mode=AuthMode.prod, owner_key_hash=hash_api_key(api_key))
    request.state.auth = context
    return context


def get_auth_context(request: Request) -> AuthContext:
    """Read the context populated by the root router dependency."""
    context = getattr(request.state, "auth", None)
    if context is None:
        raise RuntimeError("AuthContext missing on request state; authenticate_request did not run for this route")
    return context
