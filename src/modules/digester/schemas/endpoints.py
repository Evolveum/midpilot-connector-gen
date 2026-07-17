# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from src.common.schema import CamelCaseModel
from src.modules.digester.schemas.common import (
    NormalizedEndpointMethod,
    RelevantDocumentationsMixin,
)
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


# --- Endpoints ---


class EndpointRequestParameter(CamelCaseModel):
    """
    Structured request parameter available on one endpoint.

    Populated only by deterministic SCIM endpoint pregeneration
    (``extractors/scim/baseline.py``); REST documentation extraction does not produce it.
    """

    name: str
    location: Literal["path", "query", "header"]
    type: str
    description: str = ""
    required: bool = False
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    allowed_values: List[str] = Field(default_factory=list)


class EndpointParamInfo(CamelCaseModel):
    """
    LLM-editable endpoint fields (everything except path and method).

    Used directly as the structured output of the per-endpoint double-check pass,
    so the LLM can correct these fields without touching the endpoint identity.
    """

    description: str = Field(
        ...,
        description=(
            "Short summary of what this method does for the object class (e.g., 'Get user by ID', "
            "'Add user to group', 'Disable user')."
        ),
    )
    response_content_type: Optional[str] = Field(
        default=None,
        description="Primary response media type if specified (e.g., 'application/json', 'application/hal+json', 'application/vnd.oracle.resource+json', application/scim+json, other).",
    )
    request_content_type: Optional[str] = Field(
        default=None,
        description="Primary request media type if specified (often for POST/PUT/PATCH).",
    )
    suggested_use: List[EndpointSuggestedUse] = Field(
        default_factory=list,
        description="List of endpoint suggested use-cases. If unsure, leave empty.",
    )


class ExtractedEndpointInfo(EndpointParamInfo):
    """
    LLM extraction model for an HTTP endpoint associated with a specific object class.
    Contains only fields the LLM should produce.
    """

    path: str = Field(
        ...,
        description="Concrete URL path template as documented (e.g., '/users/{id}', '/users/{id}/groups').",
    )
    method: NormalizedEndpointMethod = Field(
        ...,
        description="HTTP method (e.g., GET, POST, PUT, PATCH, DELETE).",
    )


class EndpointInfo(ExtractedEndpointInfo, RelevantDocumentationsMixin):
    """
    Final API/session endpoint metadata.
    Adds system-populated fields not used in LLM extraction prompts.
    """

    parameters: List[EndpointRequestParameter] = Field(
        default_factory=list,
        description=(
            "Structured path, query and header parameters. Populated by deterministic SCIM "
            "endpoint pregeneration; empty for documentation-extracted endpoints."
        ),
    )


class EndpointResponse(CamelCaseModel):
    """
    Container for endpoints discovered for a given object class. Return an empty list when none.
    """

    endpoints: List[EndpointInfo] = Field(
        default_factory=list,
        description="List of HTTP endpoints related to the specified object class.",
    )
    scim_capabilities: Optional[ScimServiceProviderConfig] = Field(
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


# --- Connectivity Endpoint ---


class ExtractedConnectivityEndpointInfo(CamelCaseModel):
    """
    LLM extraction model for an endpoint suitable for testing connector connectivity.
    Contains only fields the LLM should produce.
    """

    path: str = Field(
        ...,
        description="Concrete URL path template as documented, normalized to start with '/' and without scheme/host.",
    )
    method: NormalizedEndpointMethod = Field(
        ...,
        description="HTTP method for the connectivity check endpoint. Prefer GET when supported by documentation.",
    )
    description: str = Field(
        ...,
        description="Short summary of why this endpoint can be used to test connectivity.",
    )
    response_content_type: Optional[str] = Field(
        default=None,
        description="Primary response media type if specified.",
    )
    request_content_type: Optional[str] = Field(
        default=None,
        description="Primary request media type if specified. Usually empty for GET connectivity checks.",
    )
    requires_auth: Optional[bool] = Field(
        default=None,
        description="Whether the endpoint requires configured authentication according to documentation.",
    )


class ConnectivityEndpointInfo(ExtractedConnectivityEndpointInfo, RelevantDocumentationsMixin):
    """
    Final API/session metadata for the endpoint selected for connectivity testing.
    Adds system-populated evidence chunk references.
    """


class RankedEndpointKey(CamelCaseModel):
    """LLM output model for a single ranked endpoint key (method + path)."""

    method: NormalizedEndpointMethod = Field(..., description="HTTP method of the endpoint.")
    path: str = Field(..., description="Normalized path of the endpoint, starting with '/'.")


class ConnectivityEndpointRankingResponse(CamelCaseModel):
    """LLM output model for ranked connectivity endpoint candidates."""

    ranked_endpoints: List[RankedEndpointKey] = Field(
        default_factory=list,
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


class ExtractedConnectivityEndpointResponse(BaseModel):
    """
    LLM extraction response for connectivity endpoint candidates.
    """

    endpoints: List[ExtractedConnectivityEndpointInfo] = Field(
        default_factory=list,
        description="Candidate HTTP endpoints that may be suitable for connectivity testing.",
    )
