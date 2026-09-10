# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, TypeAlias, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.core.schema import CamelCaseModel
from src.modules.codegen.utils.groovy_validation import ensure_valid_groovy_code
from src.modules.digester.schemas import AttributeResponse, EndpointResponse
from src.shared.auth import normalize_auth_type_value

AttributesPayload: TypeAlias = Union[AttributeResponse, Mapping[str, Any]]
EndpointsPayload: TypeAlias = Union[EndpointResponse, Mapping[str, Any]]
AuthPayload: TypeAlias = Mapping[str, Any]
PreferredAuthorizations: TypeAlias = Optional[List[Dict[str, Any]]]


@dataclass
class OperationConfig:
    """
    Static configuration of one Groovy generation operation.

    ``context_only_for_conndev`` marks a protocol whose operation context is deterministic
    (SCIM contracts, SQL tables): conndev exports are never fed to the LLM as text chunks, so
    a session built only from them would otherwise have nothing to generate from. Such a
    protocol instead runs a single pass on the extracted context alone.
    """

    operation_name: str
    system_prompt: str
    user_prompt: str
    default_scaffold: str
    logger_prefix: str
    extra_prompt_vars: Dict[str, Any] = field(default_factory=dict)
    context_only_for_conndev: bool = False


@dataclass(frozen=True)
class OperationAssets:
    """
    The prompts and bundled DSL references one operation is generated from.

    ``connid_docs_path`` is the second reference a native-schema script needs: the
    ConnID mapping lives in the same script as the attribute definitions, and the
    protocol's own schema document does not always explain the built-in ConnID
    attributes. Operations that carry no ConnID mapping leave it unset.
    """

    system_prompt: str
    user_prompt: str
    docs_path: str
    connid_docs_path: str | None = None


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


class PreferredEndpointsInput(CamelCaseModel):
    preferred_endpoints: list[PreferredEndpointsPayload] = Field(
        default_factory=list,
        description="Optional user-provided preferred endpoints used to focus code generation.",
    )

    @field_validator("preferred_endpoints", mode="before")
    @classmethod
    def normalize_preferred_endpoints(cls, value: Any) -> Any:
        if value is None:
            return []
        return value


class PreferredAuthorizationPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="Authentication/authorization method name selected by the user.")
    type: str = Field(
        ...,
        description=(
            "Authentication/authorization type, e.g. bearer, jwtBearer, oauth2ClientCredentials, or oauth2Jwt."
        ),
    )
    quirks: str | None = Field(default=None, description="Optional extracted implementation notes.")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name cannot be empty")
        return normalized

    @field_validator("type")
    @classmethod
    def normalize_type(cls, value: str) -> str:
        normalized = normalize_auth_type_value(value, preserve_unknown=True)
        if normalized is None:
            raise ValueError("type cannot be empty")
        return normalized

    @field_validator("quirks")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class PreferredAuthorizationsInput(CamelCaseModel):
    preferred_authorizations: list[PreferredAuthorizationPayload] = Field(
        ...,
        min_length=1,
        description="Required user-selected authentication/authorization methods used to focus code generation.",
    )

    @field_validator("preferred_authorizations", mode="before")
    @classmethod
    def normalize_preferred_authorizations(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return [value]
        return value


class MidpointErrorsInput(CamelCaseModel):
    """
    midPoint runtime or validation errors reported for generated code.

    Shared by the per-operation repair context and the object-class fix so
    both normalize the errors identically. Subclasses may tighten the field
    (the fix requires at least one error); the validators still apply because
    Pydantic keys them by field name.
    """

    midpoint_errors: list[str] = Field(
        default_factory=list,
        description="midPoint runtime or validation errors returned for the current script.",
    )

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


class CodegenRepairContext(MidpointErrorsInput):
    current_script: str | None = Field(
        default=None,
        description="Current user-edited Groovy script to repair.",
    )

    @field_validator("current_script")
    @classmethod
    def normalize_current_script(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @property
    def is_repair(self) -> bool:
        return bool(self.midpoint_errors)

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json", exclude_none=True)

    def repair_context(self) -> CodegenRepairContext | None:
        if not self.is_repair:
            return None
        return CodegenRepairContext(
            current_script=self.current_script,
            midpoint_errors=self.midpoint_errors,
        )

    def context_payload(self) -> dict[str, Any]:
        repair_context = self.repair_context()
        if repair_context is None:
            return {}
        return repair_context.to_payload()


class CodegenOperationInput(PreferredEndpointsInput, CodegenRepairContext):
    def preferred_endpoints_payload(self) -> list[dict[str, Any]] | None:
        if not self.preferred_endpoints:
            return None
        return [endpoint.model_dump() for endpoint in self.preferred_endpoints]


class AuthorizationCodegenInput(PreferredAuthorizationsInput, CodegenRepairContext):
    def preferred_authorizations_payload(self) -> list[dict[str, Any]]:
        return [authorization.model_dump(exclude_none=True) for authorization in self.preferred_authorizations]

    def context_payload(self) -> dict[str, Any]:
        payload = self.model_dump(
            by_alias=True,
            mode="json",
            exclude_none=True,
            exclude={"current_script", "midpoint_errors"},
        )
        if self.is_repair:
            payload.update(super().context_payload())
        return payload


class ConnectorScriptOverride(CamelCaseModel):
    """A user-edited Groovy script supplied in place of the one stored in the session."""

    operation_key: str = Field(..., description="Operation key of the script being replaced, e.g. 'userUpdate'.")
    code: str = Field(..., description="Groovy code to use instead of the stored script.")

    @field_validator("operation_key")
    @classmethod
    def validate_operation_key(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("operationKey cannot be empty")
        return normalized


class ConnectorFixInput(MidpointErrorsInput):
    """Request body for fixing generated scripts of one object class."""

    midpoint_errors: list[str] = Field(
        ...,
        min_length=1,
        description="midPoint runtime or validation errors reported for the selected object class.",
    )
    scripts: list[ConnectorScriptOverride] = Field(
        default_factory=list,
        description=(
            "Optional user-edited scripts of the selected object class that replace the stored ones "
            "as input to the fix. The session is only updated if the fix succeeds."
        ),
    )

    @model_validator(mode="after")
    def require_a_usable_error(self) -> "ConnectorFixInput":
        """
        ``min_length`` is checked before the inherited validator strips blanks, so a body
        carrying only whitespace errors would otherwise reach the fix with nothing to fix.
        """
        if not self.midpoint_errors:
            raise ValueError("midpointErrors must contain at least one non-empty error")
        return self

    @field_validator("scripts")
    @classmethod
    def reject_duplicate_operation_keys(cls, value: list[ConnectorScriptOverride]) -> list[ConnectorScriptOverride]:
        seen: set[str] = set()
        for override in value:
            if override.operation_key in seen:
                raise ValueError(f"Duplicate script override for operation '{override.operation_key}'")
            seen.add(override.operation_key)
        return value


class ConnectorFixScriptUpdate(CamelCaseModel):
    """One script the model changed. Structured output; not validated as Groovy here."""

    operation_key: str = Field(..., description="Operation key of the script you changed, copied verbatim.")
    code: str = Field(..., description="The complete fixed Groovy script for that operation.")
    reason: str = Field(..., description="One sentence: what was wrong and what you changed.")


class ConnectorFixLLMResponse(CamelCaseModel):
    """
    Structured output of one object-class fix pass.

    Groovy is deliberately not validated by a field validator: ``build_structured_chain``
    wraps the parser in ``RetryWithErrorOutputParser``, so a validator raising on one
    bad script would re-run the whole (large) call and discard the good scripts too.
    Validation happens per script after parsing.
    """

    fixed_scripts: list[ConnectorFixScriptUpdate] = Field(
        default_factory=list,
        description="Only the scripts you changed. Omit every script you are leaving as it is.",
    )
    needs_documentation: bool = Field(
        default=False,
        description="True only if you cannot fix the errors without more application documentation.",
    )
    documentation_query: str | None = Field(
        default=None,
        description="What the documentation must explain, when needsDocumentation is true.",
    )
    analysis: str | None = Field(
        default=None,
        description="Short explanation, especially when no script needed changing.",
    )


class ConnectorScript(CamelCaseModel):
    operation_key: str = Field(..., description="Operation key, e.g. 'userUpdate'.")
    session_key: str = Field(..., description="Session data key holding this script.")
    code: str = Field(..., description="Groovy code after the fix.")


class ConnectorFixChange(CamelCaseModel):
    operation_key: str = Field(..., description="Operation whose script was changed.")
    reason: str = Field(..., description="Why it was changed.")


class ConnectorFixRejection(CamelCaseModel):
    operation_key: str = Field(..., description="Operation whose proposed script was not used.")
    reason: str = Field(..., description="Why the proposed script was rejected.")


class ConnectorFixResult(CamelCaseModel):
    """Job result of an object-class connector-script fix."""

    scripts: list[ConnectorScript] = Field(
        ...,
        description="All selected object-class scripts after the fix, including unchanged scripts.",
    )
    changed_operations: list[ConnectorFixChange] = Field(
        default_factory=list, description="Operations whose scripts were changed and persisted."
    )
    rejected_scripts: list[ConnectorFixRejection] = Field(
        default_factory=list, description="Proposed scripts that were not used, with the reason."
    )
    documentation_escalated: bool = Field(
        default=False, description="Whether a second pass with session documentation was run."
    )
    documentation_query: str | None = Field(
        default=None, description="What the model asked the documentation to explain."
    )
    analysis: str | None = Field(default=None, description="The model's explanation of the fix.")
