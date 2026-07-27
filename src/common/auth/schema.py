# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import Field

from src.common.schema import CamelCaseModel


class ApiKeyCreateRequest(CamelCaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="Descriptive name for the API key")


class ApiKeyInfo(CamelCaseModel):
    """Public representation of an API key record. Never contains the key value."""

    api_key_id: UUID = Field(..., description="Unique identifier of the API key record")
    name: str = Field(..., description="Descriptive name of the API key")
    key_prefix: str = Field(..., description="First characters of the key value, for identification")
    created_at: datetime = Field(..., description="When the key was issued")
    revoked_at: Optional[datetime] = Field(None, description="When the key was revoked; null if active")


class ApiKeyCreateResponse(ApiKeyInfo):
    api_key: str = Field(..., description="Full API key value. Shown only once - it cannot be retrieved again.")
    message: str


class ApiKeyListResponse(CamelCaseModel):
    api_keys: List[ApiKeyInfo]
    total: int = Field(..., description="Total number of API key records")


class ApiKeyRevokeResponse(CamelCaseModel):
    api_key_id: UUID
    revoked_at: datetime
    message: str
