# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any, List, Optional, Tuple

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_serializer

from src.modules.digester.enums import AuthType, normalize_auth_type
from src.modules.digester.schemas.common import DocProcessingSequenceItem, DocSequenceItem

# --- Auth ---


class BaseAuth(BaseModel):
    """
    Basic authentification method class
    """

    name: str = Field(
        ...,
        description=(
            "Full name of the authentication method exactly as written in the docs/security scheme. "
            "Preserve original casing (e.g., 'BasicAuth', 'Bearer token', 'OAuth 2.0')."
        ),
    )
    type: AuthType = Field(
        ...,
        description=(
            "Normalized auth type. Allowed values: 'basic', 'bearer', 'jwtBearer', "
            "'oauth2ClientCredentials', 'oauth2Password', 'oauth2Jwt', 'oauth2Saml', "
            "'apiKey', 'session', 'digest', 'hawk', 'awsSignature', 'mtls', 'ntlm', "
            "'openidConnect', 'other'."
        ),
    )

    @field_validator("type", mode="before")
    @classmethod
    def _normalize_auth_type(cls, value: Any) -> AuthType:
        """
        Normalize auth type variations to a stable, closed vocabulary.
        """
        return normalize_auth_type(value)


class DiscoveryAuth(BaseAuth):
    """
    Authentication mechanism discovered in the API documentations/security schemes.
    Guide the LLM to extract concrete auth methods (e.g., Basic, Bearer/JWT, Session/Cookie,
    OAuth2 variants, API Key, mTLS)
    """

    # quirks: Optional[str] = Field(
    #     default="",
    #     description=(
    #         "Short, verbatim notes about special behavior or non-standard aspects (e.g., header/cookie/name, "
    #         "required scopes/realms, token prefix, custom challenge/flow). Leave empty if not applicable."
    #     ),
    # )

    relevant_sequences: List[DocSequenceItem] = Field(
        description=("List of relevant document sequences that support the presence of this auth method. "),
        validation_alias=AliasChoices("relevant_sequences", "relevantSequences"),
        serialization_alias="relevantSequences",
    )


class AuthInfo(DiscoveryAuth):
    """
    Authentication mechanism with its metadata and supporting evidence sequences.
    This is the main model used in the system for representing extracted auth methods.
    """

    quirks: Optional[str] = Field(
        default="",
        description=(
            "Short, verbatim notes about special behavior or non-standard aspects (e.g., header/cookie/name, "
            "required scopes/realms, token prefix, custom challenge/flow). Leave empty if not applicable."
        ),
    )


class AuthProcessingInfo(AuthInfo):
    """
    Authentication mechanism with full text of relevant sequences for processing in deduplication/sorting.
    This model is used internally during processing to have all necessary information in one place.
    """

    relevant_sequences: List[DocProcessingSequenceItem] = Field(  # type: ignore[assignment]
        description=("List of document sequences that support the presence of this auth method, includes full text")
    )


class AuthDedupResponse(BaseModel):
    """
    Container for deduplication LLM output.
    """

    duplicates: List[Tuple[Tuple[str, str], Tuple[str, str]]] = Field(
        ...,
        description=(
            "List of pairs of duplicate auth methods. Each pair contains two tuples: (name, type) of the auth method."
        ),
    )

    to_be_deleted: List[Tuple[str, str]] = Field(
        ...,
        description=(
            "List of auth methods (Tuples of name and type) to be deleted because of having weak documentation"
        ),
    )


class AuthDiscoveryResponse(BaseModel):
    """
    Container for extracted authentication mechanisms in discovery. Return an empty list when none are present.
    """

    auth: Optional[List[DiscoveryAuth]] = Field(
        default_factory=list,
        description="List of authentication methods supported or referenced by the API.",
    )

    model_config = {"populate_by_name": True}

    # Ensure robustness: coerce null to [] and never serialize null
    @field_validator("auth", mode="before")
    @classmethod
    def _normalize_auth(cls, v):
        if v is None:
            return []
        return v

    @model_serializer
    def _serialize(self):
        # Always emit [] instead of null to keep contract stable
        return {"auth": self.auth or []}


class AuthBuildResponse(BaseAuth):
    """
    Container for extracted authentication mechanisms after building the auth info
    """

    quirks: Optional[str] = Field(
        default="",
        description=(
            "Short, verbatim notes about special behavior or non-standard aspects (e.g., header/cookie/name, "
            "required scopes/realms, token prefix, custom challenge/flow). Leave empty if not applicable."
        ),
    )


class AuthResponse(BaseModel):
    """
    Container for extracted authentication mechanisms. Return an empty list when none are present.
    """

    auth: Optional[List[AuthInfo]] = Field(
        default_factory=list,
        description="List of authentication methods supported or referenced by the API.",
    )

    model_config = {"populate_by_name": True}

    # Ensure robustness: coerce null to [] and never serialize null
    @field_validator("auth", mode="before")
    @classmethod
    def _normalize_auth(cls, v):
        if v is None:
            return []
        return v

    @model_serializer
    def _serialize(self):
        # Always emit [] instead of null to keep contract stable
        return {"auth": self.auth or []}
