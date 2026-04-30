# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    model_config = ConfigDict(populate_by_name=True)

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


class CodegenRepairContext(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    current_script: str | None = Field(
        default=None,
        validation_alias="currentScript",
        serialization_alias="currentScript",
        description="Current user-edited Groovy script to repair.",
    )
    midpoint_errors: list[str] = Field(
        default_factory=list,
        validation_alias="midpointErrors",
        serialization_alias="midpointErrors",
        description="midPoint runtime or validation errors returned for the current script.",
    )

    @field_validator("current_script")
    @classmethod
    def normalize_current_script(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("midpoint_errors", mode="before")
    @classmethod
    def normalize_midpoint_errors(cls, value: Any) -> Any:
        if value is None:
            return []
        return value

    @field_validator("midpoint_errors")
    @classmethod
    def validate_midpoint_errors(cls, value: list[str]) -> list[str]:
        return [error.strip() for error in value if error.strip()]

    @property
    def is_repair(self) -> bool:
        return bool(self.midpoint_errors)

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json", exclude_none=True)


class CodegenOperationInput(PreferredEndpointsInput, CodegenRepairContext):
    def repair_context(self) -> CodegenRepairContext | None:
        if not self.is_repair:
            return None
        return CodegenRepairContext(
            current_script=self.current_script,
            midpoint_errors=self.midpoint_errors,
        )

    def context_payload(self) -> dict[str, Any]:
        if not self.is_repair:
            return {}
        return CodegenRepairContext(
            current_script=self.current_script,
            midpoint_errors=self.midpoint_errors,
        ).to_payload()
