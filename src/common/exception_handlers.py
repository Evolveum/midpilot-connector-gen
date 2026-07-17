# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.common.errors import AppError

logger = logging.getLogger(__name__)


def _error_body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


async def _handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    """Map a domain error to its HTTP response.

    The parameter is typed as ``Exception`` to match the signature expected by
    Starlette's ``add_exception_handler``; this handler is only ever registered
    for ``AppError``, so the narrowing is safe.

    Client errors (4xx) are logged at warning level; server errors (5xx) are
    logged with a full traceback since they indicate a problem on our side.
    """
    error = cast(AppError, exc)
    if error.status_code >= 500:
        logger.exception("[%s %s] %s", request.method, request.url.path, error.code)
    else:
        logger.warning("[%s %s] %s: %s", request.method, request.url.path, error.code, error.message)
    return JSONResponse(status_code=error.status_code, content=_error_body(error.code, error.message))


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler so no unexpected error leaks internals to the client.

    The full traceback is logged; the client receives a generic 500.
    """
    logger.exception("[%s %s] Unhandled exception", request.method, request.url.path)
    return JSONResponse(status_code=500, content=_error_body("internal_error", "Internal server error"))


def register_exception_handlers(app: FastAPI) -> None:
    """Register the centralized exception handlers on the FastAPI app."""
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(Exception, _handle_unexpected)
