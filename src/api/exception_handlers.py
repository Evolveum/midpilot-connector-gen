# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from collections.abc import Sequence
from typing import cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.core.errors import AppError

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
    _log_app_error(request, error)
    return JSONResponse(status_code=error.status_code, content=_error_body(error.code, error.message))


def _log_app_error(request: Request, error: AppError) -> None:
    """Log client errors (4xx) at warning level and server errors with a traceback."""
    if error.status_code >= 500:
        logger.exception("[%s %s] %s", request.method, request.url.path, error.code)
    else:
        logger.warning("[%s %s] %s: %s", request.method, request.url.path, error.code, error.message)


async def _handle_detail_envelope_error(request: Request, exc: Exception) -> JSONResponse:
    """Map a domain error whose established response is FastAPI's ``{"detail": ...}`` envelope.

    Registered only for the error types the composition root lists, so the narrowing
    to ``AppError`` is safe. Status and logging follow the regular domain-error rules;
    only the response body keeps the shape those consumers already parse.
    """
    error = cast(AppError, exc)
    _log_app_error(request, error)
    return JSONResponse(status_code=error.status_code, content={"detail": error.message})


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler so no unexpected error leaks internals to the client.

    The full traceback is logged; the client receives a generic 500.
    """
    logger.exception("[%s %s] Unhandled exception", request.method, request.url.path)
    return JSONResponse(status_code=500, content=_error_body("internal_error", "Internal server error"))


def register_exception_handlers(
    app: FastAPI,
    *,
    detail_envelope_errors: Sequence[type[AppError]] = (),
) -> None:
    """Register the centralized exception handlers on the FastAPI app.

    ``detail_envelope_errors`` names domain errors (and their subclasses) whose
    response contract predates the ``{"error": {code, message}}`` envelope. The
    composition root supplies them, so this layer never imports the domains that
    define them. Starlette resolves handlers along the exception's MRO, so these
    registrations win over the generic ``AppError`` handler.
    """
    app.add_exception_handler(AppError, _handle_app_error)
    for error_type in detail_envelope_errors:
        app.add_exception_handler(error_type, _handle_detail_envelope_error)
    app.add_exception_handler(Exception, _handle_unexpected)
