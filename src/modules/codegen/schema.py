# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.
from dataclasses import dataclass
from typing import Any, Dict

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


class PreferredEndpointInput(BaseModel):
    preferred_endpoint: Dict[str, Any] | None = Field(
        default=None,
        validation_alias="preferredEndpoint",
        serialization_alias="preferredEndpoint",
        description="Optional user-provided preferred endpoint used to focus code generation.",
    )
