# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Session attribution for the log records a request produces.

Registered in ``src.app`` as a router-level dependency. Every session route uses
the uniform ``session_id`` path parameter, so - like
``src.session.ownership.enforce_session_ownership`` - the binding reads it from
``request.path_params`` and no route needs individual wiring.
"""

import logging
from typing import AsyncIterator
from uuid import UUID

from fastapi import Request

from src.core.observability.correlation import bind_request_session, reset_request_session

logger = logging.getLogger(__name__)


async def bind_session_correlation(request: Request) -> AsyncIterator[None]:
    """Attribute this request's log records to the session it addresses.

    Routes without a ``session_id`` (API key management, health) and malformed
    ids are left unattributed rather than guessed; FastAPI's own path validation
    reports the malformed case to the client.
    """
    raw_session_id = request.path_params.get("session_id")
    if raw_session_id is None:
        yield
        return

    try:
        session_id = UUID(str(raw_session_id))
    except ValueError:
        yield
        return

    token = bind_request_session(session_id)
    try:
        yield
    finally:
        reset_request_session(token)
