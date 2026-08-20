# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Optional

from pydantic import BaseModel, SecretStr, model_validator


class AuthSettings(BaseModel):
    """
    API key authentication settings.

    :param api_key_required: When True, every API request must present a valid
        API key in the ``X-API-Key`` header. When False, all requests are
        accepted without authentication (development mode).
    :param master_api_key: Statically configured administrative key. It grants
        access to every session and is the only key allowed to manage
        (issue/revoke) other API keys. Required when api_key_required is True.
    """

    api_key_required: bool = False
    master_api_key: Optional[SecretStr] = None

    @model_validator(mode="after")
    def _require_master_key_when_enforcing(self) -> "AuthSettings":
        if self.api_key_required and not (self.master_api_key and self.master_api_key.get_secret_value()):
            raise ValueError(
                "AUTH__MASTER_API_KEY must be configured when AUTH__API_KEY_REQUIRED is enabled: "
                "without it no API keys could ever be issued."
            )
        return self
