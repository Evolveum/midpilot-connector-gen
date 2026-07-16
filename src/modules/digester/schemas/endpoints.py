# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from src.modules.digester.enums import EndpointMethod
from src.modules.digester.schemas.common import RelevantDocumentationsMixin
from src.modules.digester.schemas.scim import ScimServiceProviderConfig

EndpointSuggestedUse = Literal[
    "create",
    "update",
    "delete",
    "getById",
    "getAll",
    "list",
    "search",
    "activate",
    "deactivate",
]


class EndpointRequestParameter(BaseModel):
    """Structured request parameter available on one endpoint."""

    model_config = {"populate_by_name": True}

    name: str
    location: Literal["path", "query", "header"]
    type: str
    description: str = ""
    required: bool = False
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    allowed_values: List[str] = Field(
        default_factory=list,
        validation_alias="allowedValues",
        serialization_alias="allowedValues",
    )


class ExtractedEndpointInfo(BaseModel):
    """
    LLM extraction model for an HTTP endpoint associated with a specific object class.
    Contains only fields the LLM should produce.
    """

    model_config = {"populate_by_name": True}

    path: str = Field(
        ...,
        description="Concrete URL path template as documented (e.g., '/users/{id}', '/users/{id}/groups').",
    )
    method: EndpointMethod = Field(
        ...,
        description="HTTP method (e.g., GET, POST, PUT, PATCH, DELETE).",
    )
    description: str = Field(
        ...,
        description=(
            "Short summary of what this method does for the object class (e.g., 'Get user by ID', "
            "'Add user to group', 'Disable user')."
        ),
    )
    response_content_type: Optional[str] = Field(
        default=None,
        validation_alias="responseContentType",
        serialization_alias="responseContentType",
        description="Primary response media type if specified (e.g., 'application/json', 'application/hal+json', 'application/vnd.oracle.resource+json', application/scim+json, other).",
    )
    request_content_type: Optional[str] = Field(
        default=None,
        validation_alias="requestContentType",
        serialization_alias="requestContentType",
        description="Primary request media type if specified (often for POST/PUT/PATCH).",
    )
    suggested_use: List[EndpointSuggestedUse] = Field(
        default_factory=list,
        validation_alias="suggestedUse",
        serialization_alias="suggestedUse",
        description="List of endpoint suggested use-cases. Allowed values: 'create', 'update', 'delete', 'getById', 'getAll', 'list', 'search', 'activate', 'deactivate'. If unsure, leave empty.",
    )
    parameters: List[EndpointRequestParameter] = Field(
        default_factory=list,
        description="Documented path, query and header parameters available on this endpoint.",
    )

    @field_validator("method", mode="before")
    @classmethod
    def _normalize_method(cls, value: Any) -> Any:
        """Accept lowercase/mixed-case methods and normalize them before literal validation."""
        if isinstance(value, str):
            return value.strip().upper()
        return value


class EndpointInfo(ExtractedEndpointInfo, RelevantDocumentationsMixin):
    """
    Final API/session endpoint metadata.
    Adds system-populated fields not used in LLM extraction prompts.
    """


class EndpointParamInfo(BaseModel):
    """
    EndpointInfo without path and method, so the LLM can only modify other fields.
    """

    model_config = {"populate_by_name": True}

    description: str = Field(
        ...,
        description=(
            "Short summary of what this method does for the object class (e.g., 'Get user by ID', "
            "'Add user to group', 'Disable user')."
        ),
    )
    response_content_type: Optional[str] = Field(
        default=None,
        validation_alias="responseContentType",
        serialization_alias="responseContentType",
        description="Primary response media type if specified (e.g., 'application/json', 'application/hal+json', 'application/vnd.oracle.resource+json', application/scim+json, other).",
    )
    request_content_type: Optional[str] = Field(
        default=None,
        validation_alias="requestContentType",
        serialization_alias="requestContentType",
        description="Primary request media type if specified (often for POST/PUT/PATCH).",
    )
    suggested_use: List[EndpointSuggestedUse] = Field(
        default_factory=list,
        validation_alias="suggestedUse",
        serialization_alias="suggestedUse",
        description="List of endpoint suggested use-cases. Allowed values: 'create', 'update', 'delete', 'getById', 'getAll', 'search', 'activate', 'deactivate'. If unsure, leave empty.",
    )
    parameters: List[EndpointRequestParameter] = Field(
        default_factory=list,
        description="Documented path, query and header parameters available on this endpoint.",
    )


class EndpointResponse(BaseModel):
    """
    Container for endpoints discovered for a given object class. Return an empty list when none.
    """

    endpoints: List[EndpointInfo] = Field(
        default_factory=list,
        description="List of HTTP endpoints related to the specified object class.",
    )
    scimCapabilities: Optional[ScimServiceProviderConfig] = Field(
        default=None,
        description="Session-wide SCIM service-provider capabilities used to derive the endpoint surface.",
    )


class ExtractedEndpointResponse(BaseModel):
    """
    LLM extraction response for endpoints.
    """

    endpoints: List[ExtractedEndpointInfo] = Field(
        default_factory=list,
        description="List of extracted HTTP endpoints related to the specified object class.",
    )


# --- Endpoints ---


# --- Connectivity Endpoint ---


class ExtractedConnectivityEndpointInfo(BaseModel):
    """
    LLM extraction model for an endpoint suitable for testing connector connectivity.
    Contains only fields the LLM should produce.
    """

    model_config = {"populate_by_name": True}

    path: str = Field(
        ...,
        description="Concrete URL path template as documented, normalized to start with '/' and without scheme/host.",
    )
    method: EndpointMethod = Field(
        ...,
        description="HTTP method for the connectivity check endpoint. Prefer GET when supported by documentation.",
    )
    description: str = Field(
        ...,
        description="Short summary of why this endpoint can be used to test connectivity.",
    )
    response_content_type: Optional[str] = Field(
        default=None,
        validation_alias="responseContentType",
        serialization_alias="responseContentType",
        description="Primary response media type if specified.",
    )
    request_content_type: Optional[str] = Field(
        default=None,
        validation_alias="requestContentType",
        serialization_alias="requestContentType",
        description="Primary request media type if specified. Usually empty for GET connectivity checks.",
    )
    requires_auth: Optional[bool] = Field(
        default=None,
        validation_alias="requiresAuth",
        serialization_alias="requiresAuth",
        description="Whether the endpoint requires configured authentication according to documentation.",
    )

    @field_validator("method", mode="before")
    @classmethod
    def _normalize_method(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().upper()
        return value


class ConnectivityEndpointInfo(ExtractedConnectivityEndpointInfo, RelevantDocumentationsMixin):
    """
    Final API/session metadata for the endpoint selected for connectivity testing.
    Adds system-populated evidence chunk references.
    """


class RankedEndpointKey(BaseModel):
    """LLM output model for a single ranked endpoint key (method + path)."""

    model_config = {"populate_by_name": True}

    method: EndpointMethod = Field(..., description="HTTP method of the endpoint.")
    path: str = Field(..., description="Normalized path of the endpoint, starting with '/'.")

    @field_validator("method", mode="before")
    @classmethod
    def _normalize_method(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().upper()
        return value


class ConnectivityEndpointRankingResponse(BaseModel):
    """LLM output model for ranked connectivity endpoint candidates."""

    ranked_endpoints: List[RankedEndpointKey] = Field(
        default_factory=list,
        validation_alias="rankedEndpoints",
        serialization_alias="rankedEndpoints",
        description="Endpoints ranked by suitability for connectivity testing, most suitable first.",
    )


class ConnectivityEndpointResponse(BaseModel):
    """
    Ranked list of endpoints for testing connectivity between midPoint connector generator and the target application.
    Empty list when no suitable endpoint is documented. First endpoint is the most suitable.
    """

    endpoints: List[ConnectivityEndpointInfo] = Field(
        default_factory=list,
        description="Ranked list of documented endpoints for connectivity checks, most suitable first.",
    )

    model_config = {"populate_by_name": True}


class ExtractedConnectivityEndpointResponse(BaseModel):
    """
    LLM extraction response for connectivity endpoint candidates.
    """

    endpoints: List[ExtractedConnectivityEndpointInfo] = Field(
        default_factory=list,
        description="Candidate HTTP endpoints that may be suitable for connectivity testing.",
    )


# --- Connectivity Endpoint ---
