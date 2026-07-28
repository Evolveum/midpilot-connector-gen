# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from uuid import UUID

from src.core.errors import AppError


class MissingApiKeyError(AppError):
    """Raised when API key authentication is required but no key was presented."""

    status_code = 401
    code = "missing_api_key"

    def __init__(self) -> None:
        super().__init__("Missing API key. Provide a valid key in the X-API-Key header.")


class InvalidApiKeyError(AppError):
    """Raised when the presented API key is unknown or has been revoked."""

    status_code = 401
    code = "invalid_api_key"

    def __init__(self) -> None:
        super().__init__("Invalid or revoked API key.")


class MasterKeyRequiredError(AppError):
    """Raised when an API key management endpoint is called without the master key."""

    status_code = 403
    code = "master_key_required"

    def __init__(self, detail: str | None = None):
        super().__init__(detail or "API key management requires the master API key.")


class ApiKeyNotFoundError(AppError):
    """Raised when a referenced API key record does not exist."""

    status_code = 404
    code = "api_key_not_found"

    def __init__(self, api_key_id: UUID):
        super().__init__(f"API key {api_key_id} not found")
