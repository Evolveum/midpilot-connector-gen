# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, field_validator

from src.modules.codegen.utils.groovy_validation import ensure_valid_groovy_code


@dataclass(frozen=True)
class OperationAssets:
    system_prompt: str
    user_prompt: str
    docs_path: str


class GroovyCodePayload(BaseModel):
    code: str = Field(..., description="Groovy code")

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        return ensure_valid_groovy_code(value)


class PreferredEndpointsPayload(BaseModel):
    method: str = Field(..., description="HTTP method of the preferred endpoint.")
    path: str = Field(..., description="Path of the preferred endpoint.")

    @field_validator("method")
    @classmethod
    def validate_method(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("method cannot be empty")
        return normalized

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("path cannot be empty")
        return normalized


class PreferredEndpointsInput(BaseModel):
    preferred_endpoints: list[PreferredEndpointsPayload] = Field(
        default_factory=list,
        validation_alias="preferredEndpoints",
        serialization_alias="preferredEndpoints",
        description="Optional user-provided preferred endpoints used to focus code generation.",
    )

    @field_validator("preferred_endpoints", mode="before")
    @classmethod
    def normalize_preferred_endpoints(cls, value: Any) -> Any:
        if value is None:
            return []
        return value
