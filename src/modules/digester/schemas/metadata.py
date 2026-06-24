# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_serializer

from src.common.enums import ApiType, ScimAvailability, ScimSource
from src.modules.digester.enums import EndpointType


# ---  Info about schema ---
class BaseAPIEndpoint(BaseModel):
    """
    Base API endpoint for the product. Distinguish between constant URLs and tenant-specific (dynamic) URLs.
    """

    uri: str = Field(..., description="Base URL or URI template to call the API (e.g., https://host/api/v1).")
    type: EndpointType = Field(
        default=EndpointType.UNKNOWN,
        description=(
            "'constant' if same for all deployments; 'dynamic' if varies per tenant/installation; "
            "empty string when unknown."
        ),
    )

    model_config = {"populate_by_name": True}

    @field_validator("type", mode="before")
    @classmethod
    def _normalize_type(cls, value: Any) -> EndpointType:
        if isinstance(value, EndpointType):
            return value
        if value is None:
            return EndpointType.UNKNOWN
        if not isinstance(value, str):
            return EndpointType.UNKNOWN

        normalized = value.strip().lower()
        if normalized in {EndpointType.CONSTANT.value, EndpointType.DYNAMIC.value}:
            return EndpointType(normalized)
        return EndpointType.UNKNOWN


def normalize_api_type_values(value: Any) -> List[ApiType]:
    """
    Normalize api types from various upstream sources.
    Keep only supported values and canonicalize their casing.
    """
    if value is None:
        return []

    raw_values: List[Any]
    if isinstance(value, str):
        raw_values = [value]
    elif isinstance(value, list):
        raw_values = value
    else:
        return []

    aliases: Dict[str, ApiType] = {
        "rest": ApiType.REST,
        "openapi": ApiType.REST,
        "swagger": ApiType.REST,
        "scim": ApiType.SCIM,
        "sql": ApiType.SQL,
        "db": ApiType.SQL,
    }

    normalized: List[ApiType] = []
    for item in raw_values:
        if not isinstance(item, str):
            continue
        canonical = aliases.get(item.strip().lower())
        if canonical:
            normalized.append(canonical)

    # Preserve the first-seen order while deduplicating.
    return list(dict.fromkeys(normalized))


class InfoMetadataExtraction(BaseModel):
    """
    High-level product and API metadata extracted from documentation chunks.
    """

    name: str = Field(
        default="",
        description="Application/product name as stated in the docs.",
    )
    application_version: Optional[str] = Field(
        default="",
        validation_alias="applicationVersion",
        serialization_alias="applicationVersion",
        description="Application version label if provided.",
    )
    api_version: str = Field(
        default="",
        validation_alias="apiVersion",
        serialization_alias="apiVersion",
        description="API version string as documented (e.g., 'v1', '2024-05', semantic).",
    )
    base_api_endpoint: List[BaseAPIEndpoint] = Field(
        default_factory=list,
        validation_alias="baseApiEndpoint",
        serialization_alias="baseApiEndpoint",
        description="One or more base endpoints/URI templates with their constant/dynamic classification.",
    )
    database_name: str = Field(
        default="",
        validation_alias="databaseName",
        serialization_alias="databaseName",
        description=(
            "Database/schema name the connector must connect to. Populate ONLY for SQL/database integrations; "
            "leave empty for REST/SCIM."
        ),
    )

    model_config = {"populate_by_name": True}

    @field_validator("base_api_endpoint", mode="before")
    @classmethod
    def _normalize_base_api_endpoint(cls, v: Any) -> List[Any]:
        """
        Keep the field resilient to partial/malformed LLM output.
        Accept null, a single object, or a list of objects.
        """
        if v is None:
            return []
        if isinstance(v, dict) or isinstance(v, BaseAPIEndpoint):
            return [v]
        if isinstance(v, list):
            return [item for item in v if isinstance(item, (dict, BaseAPIEndpoint))]
        return []

    @field_validator("base_api_endpoint", mode="after")
    @classmethod
    def _dedupe_and_sort_base_api_endpoint(cls, endpoints: List[BaseAPIEndpoint]) -> List[BaseAPIEndpoint]:
        unique: Dict[tuple[str, EndpointType], BaseAPIEndpoint] = {}
        for endpoint in endpoints or []:
            uri = (endpoint.uri or "").strip()
            if not uri:
                continue
            key = (uri.lower(), endpoint.type)
            if key not in unique:
                unique[key] = BaseAPIEndpoint(uri=uri, type=endpoint.type)

        return sorted(
            unique.values(),
            key=lambda endpoint: (endpoint.uri.lower(), 0 if endpoint.type == EndpointType.CONSTANT else 1),
        )


# TODO
# In the future, this will be calculated from signal agreement
DEFAULT_SCIM_AVAILABILITY_CONFIDENCE: float = 1.0


class ScimAvailabilityInfo(BaseModel):
    """
    Advisory SCIM availability surfaced on the API response when SCIM is detected.

    SCIM may exist for a product yet require a paid/enterprise plan the customer might not
    have. Aggregated from the documentation-free SCIM signals; included only when ``scim``
    is in ``apiType`` (dropped otherwise, like ``databaseName`` for non-SQL).
    """

    status: ScimAvailability = Field(
        default=ScimAvailability.UNKNOWN,
        description="SCIM availability: 'available', 'paid', or 'unknown'.",
    )
    required_plan: str = Field(
        default="",
        validation_alias="requiredPlan",
        serialization_alias="requiredPlan",
        description="Plan/tier required when status is 'paid' (e.g. 'Enterprise'); empty when unknown.",
    )
    sources: List[ScimSource] = Field(
        default_factory=list,
        description="Signals that confirmed SCIM: scim_cloud, documentation, knowledge, web_search.",
    )
    confidence: float = Field(
        default=DEFAULT_SCIM_AVAILABILITY_CONFIDENCE,
        ge=0.0,
        le=1.0,
        description="Confidence in [0, 1]. Placeholder default until derived from signal agreement.",
    )

    model_config = {"populate_by_name": True}


class InfoMetadata(InfoMetadataExtraction):
    """
    Final high-level product and API metadata, including the detected ``apiType``.

    This is the stored/returned payload. ``apiType`` is filled by the dedicated
    detection pipeline and merged in by the service layer, not by chunk extraction.
    """

    api_type: List[ApiType] = Field(
        default_factory=list,
        validation_alias="apiType",
        serialization_alias="apiType",
        description=(
            "API technology types. Allowed values: REST, SCIM, SQL. OpenAPI/Swagger should be normalized to REST."
        ),
    )
    scim_availability: Optional[ScimAvailabilityInfo] = Field(
        default=None,
        validation_alias="scimAvailability",
        serialization_alias="scimAvailability",
        description="SCIM availability advisory; present only when 'scim' is in apiType.",
    )

    @field_validator("api_type", mode="before")
    @classmethod
    def _normalize_api_type(cls, value: Any) -> List[ApiType]:
        return normalize_api_type_values(value)

    @model_serializer(mode="wrap")
    def _serialize_for_api_type(self, handler: Any) -> Any:
        """
        Drop fields that are irrelevant for the detected apiType on output:
        - ``databaseName`` is kept only for SQL integrations.
        - ``baseApiEndpoint`` is dropped for SQL-only integrations (no REST/SCIM).
        """
        data = handler(self)
        if not isinstance(data, dict):
            return data

        is_sql = ApiType.SQL in self.api_type
        is_scim = ApiType.SCIM in self.api_type
        is_rest_or_scim = any(api_type in (ApiType.REST, ApiType.SCIM) for api_type in self.api_type)

        if not is_sql:
            data.pop("databaseName", None)
            data.pop("database_name", None)

        if is_sql and not is_rest_or_scim:
            data.pop("baseApiEndpoint", None)
            data.pop("base_api_endpoint", None)

        if not is_scim:
            data.pop("scimAvailability", None)
            data.pop("scim_availability", None)

        return data


class ApiTypeResponse(BaseModel):
    """
    Structured output for the standalone apiType detection LLM call.

    Runs as its own per-chunk extraction, separate from the generic info metadata
    extraction, and is merged into the final ``InfoMetadata.apiType``.
    """

    api_type: List[ApiType] = Field(
        default_factory=list,
        validation_alias="apiType",
        serialization_alias="apiType",
        description="API technology types detected for this fragment. Allowed values: REST, SCIM, SQL.",
    )

    model_config = {"populate_by_name": True}

    @field_validator("api_type", mode="before")
    @classmethod
    def _normalize_api_type(cls, value: Any) -> List[ApiType]:
        return normalize_api_type_values(value)


class ApiTypeSignalResult(BaseModel):
    """
    Structured output shared by the documentation-free SCIM apiType signals
    (knowledge-based and web-search-based).

    Both signals answer the same questions about a single application name: whether it
    exposes SCIM, which integration protocol types it has, and whether SCIM is generally
    available or restricted to a paid/enterprise plan.
    """

    supports_scim: bool = Field(
        default=False,
        validation_alias="supportsScim",
        serialization_alias="supportsScim",
        description="True only when the named application is known to expose a SCIM provisioning API.",
    )
    api_type: List[ApiType] = Field(
        default_factory=list,
        validation_alias="apiType",
        serialization_alias="apiType",
        description="Integration protocol types the application is known to support. Allowed values: REST, SCIM, SQL.",
    )
    scim_availability: ScimAvailability = Field(
        default=ScimAvailability.UNKNOWN,
        validation_alias="scimAvailability",
        serialization_alias="scimAvailability",
        description=(
            "Whether SCIM is generally available ('available'), restricted to a paid/enterprise tier ('paid'), "
            "or not determinable ('unknown'). Use 'unknown' when unsure."
        ),
    )
    required_plan: str = Field(
        default="",
        validation_alias="requiredPlan",
        serialization_alias="requiredPlan",
        description="Plan/tier required for SCIM when it is paid (e.g. 'Enterprise Grid'); empty otherwise.",
    )

    model_config = {"populate_by_name": True}

    @field_validator("api_type", mode="before")
    @classmethod
    def _normalize_api_type(cls, value: Any) -> List[ApiType]:
        return normalize_api_type_values(value)

    @field_validator("scim_availability", mode="before")
    @classmethod
    def _normalize_scim_availability(cls, value: Any) -> ScimAvailability:
        if isinstance(value, ScimAvailability):
            return value
        if not isinstance(value, str):
            return ScimAvailability.UNKNOWN
        mapping = {
            "available": ScimAvailability.AVAILABLE,
            "free": ScimAvailability.AVAILABLE,
            "included": ScimAvailability.AVAILABLE,
            "standard": ScimAvailability.AVAILABLE,
            "paid": ScimAvailability.PAID,
            "gated": ScimAvailability.PAID,
            "premium": ScimAvailability.PAID,
            "enterprise": ScimAvailability.PAID,
            "business": ScimAvailability.PAID,
            "unknown": ScimAvailability.UNKNOWN,
        }
        return mapping.get(value.strip().lower(), ScimAvailability.UNKNOWN)


class InfoExtractionResponse(BaseModel):
    """
    Container for per-chunk info metadata extraction (apiType handled separately).
    """

    info_metadata: Optional[InfoMetadataExtraction] = Field(
        default=None,
        validation_alias="infoMetadata",
        serialization_alias="infoMetadata",
        description="High-level application metadata if discovered in the documentation. Null when unavailable.",
    )

    model_config = {"populate_by_name": True}

    @field_validator("info_metadata", mode="before")
    @classmethod
    def _normalize_info(cls, v):
        if v is None:
            return None
        return v


class InfoResponse(BaseModel):
    """
    Container for high-level API metadata. Return null/empty fields when unknown.
    Use the alias 'InfoMetadata' for serialization if needed.
    """

    info_metadata: Optional[InfoMetadata] = Field(
        default=None,
        validation_alias="infoMetadata",
        serialization_alias="infoMetadata",
        description="High-level application and API metadata if discovered in the documentations. Null when unavailable.",
    )

    model_config = {"populate_by_name": True}

    @field_validator("info_metadata", mode="before")
    @classmethod
    def _normalize_info(cls, v):
        if v is None:
            return None
        return v
