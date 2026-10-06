# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.core.errors import AppError


class MissingApiKeyError(AppError):
    status_code = 401
    code = "missing_api_key"

    def __init__(self) -> None:
        super().__init__("Missing API key. Call through Gravitee with the X-Gravitee-Api-Key header.")


class InvalidApiKeyError(AppError):
    """Malformed or ambiguous forwarding, not a gateway validity decision."""

    status_code = 401
    code = "invalid_api_key"

    def __init__(self) -> None:
        super().__init__("Expected one non-empty X-Gravitee-Api-Key header without whitespace or commas.")
