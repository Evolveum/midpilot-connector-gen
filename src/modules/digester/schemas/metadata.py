# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator

from src.core.schema import CamelCaseModel
from src.modules.digester.enums import EndpointType
from src.shared.enums import ApiType, DetectionSource, ProtocolAvailability

# Shared alias table for canonicalizing API technology types from upstream sources.
API_TYPE_ALIASES: Dict[str, ApiType] = {
    "rest": ApiType.REST,
    "openapi": ApiType.REST,
    "swagger": ApiType.REST,
    "scim": ApiType.SCIM,
    "sql": ApiType.SQL,
    "db": ApiType.SQL,
}

# HTTP base endpoints belong to an HTTP protocol; SQL integrations have no base endpoint.
ENDPOINT_PROTOCOLS: tuple[ApiType, ...] = (ApiType.REST, ApiType.SCIM)


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

    normalized: List[ApiType] = []
    for item in raw_values:
        if isinstance(item, ApiType):
            normalized.append(item)
            continue
        if not isinstance(item, str):
            continue
        canonical = API_TYPE_ALIASES.get(item.strip().lower())
        if canonical:
            normalized.append(canonical)

    # Preserve the first-seen order while deduplicating.
    return list(dict.fromkeys(normalized))


def normalize_endpoint_protocol(value: Any) -> ApiType | None:
    """
    Normalize the explicit HTTP protocol a base endpoint serves to REST or SCIM.

    Missing, SQL, or unknown values remain unclassified so merge logic can fall back
    to the session-level apiType before defaulting to REST.
    """
    canonical: ApiType | None
    if isinstance(value, ApiType):
        canonical = value
    elif isinstance(value, str):
        stripped = value.strip().lower()
        if not stripped:
            return None
        canonical = API_TYPE_ALIASES.get(stripped)
    else:
        return None
    return canonical if canonical in ENDPOINT_PROTOCOLS else None


def coerce_base_api_endpoint_list(value: Any) -> List[Any]:
    """
    Keep base-endpoint fields resilient to partial/malformed upstream output.
    Accept null, a single object, or a list of objects.
    """
    if value is None:
        return []
    if isinstance(value, (dict, BaseAPIEndpoint)):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, (dict, BaseAPIEndpoint))]
    return []


# ---  Info about schema ---
class BaseAPIEndpoint(CamelCaseModel):
    """
    Base API endpoint for the product. Distinguishes constant vs tenant-specific (dynamic)
    URLs, and which HTTP protocol (REST/SCIM) the endpoint serves.
    """

    uri: str = Field(..., description="Base URL or URI template to call the API (e.g., https://host/api/v1).")
    type: EndpointType = Field(
        default=EndpointType.UNKNOWN,
        description=(
            "'constant' if same for all deployments; 'dynamic' if varies per tenant/installation; "
            "empty string when unknown."
        ),
    )
    api_type: ApiType | None = Field(
        default=None,
        exclude=True,
        description=(
            "Explicit HTTP protocol this base endpoint serves: 'rest' or 'scim'. When omitted, merge logic routes the "
            "endpoint from the session-level apiType; not serialized (the block already implies the protocol)."
        ),
    )

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

    @field_validator("api_type", mode="before")
    @classmethod
    def _normalize_api_type(cls, value: Any) -> ApiType | None:
        return normalize_endpoint_protocol(value)


class InfoMetadataBase(CamelCaseModel):
    """
    Scalar product/API identity fields shared by per-chunk extraction and the final payload.
    """

    name: str = Field(
        default="",
        description="Application/product name as stated in the docs.",
    )
    application_version: Optional[str] = Field(
        default="",
        description="Application version label if provided.",
    )
    api_version: str = Field(
        default="",
        description="API version string as documented (e.g., 'v1', '2024-05', semantic).",
    )


class _EndpointCarrier(CamelCaseModel):
    """
    Shared base-endpoint field and input normalization for metadata models.
    """

    base_api_endpoint: List[BaseAPIEndpoint] = Field(
        default_factory=list,
        description="Base endpoints/URI templates with their constant/dynamic and REST/SCIM classification.",
    )

    @field_validator("base_api_endpoint", mode="before")
    @classmethod
    def _normalize_base_api_endpoint(cls, v: Any) -> List[Any]:
        return coerce_base_api_endpoint_list(v)


class InfoMetadataExtraction(_EndpointCarrier, InfoMetadataBase):
    """
    High-level product and API metadata extracted from a single documentation chunk.

    The per-chunk LLM contract is intentionally flat: base endpoints (each tagged with the
    HTTP protocol it serves) and an optional database name live side by side. The service
    layer regroups these into protocol-specific availability blocks on the final payload.
    """

    database_name: str = Field(
        default="",
        description=(
            "Database/schema name the connector must connect to. Populate ONLY for SQL/database integrations; "
            "leave empty for REST/SCIM."
        ),
    )

    @field_validator("base_api_endpoint", mode="after")
    @classmethod
    def _dedupe_and_sort_base_api_endpoint(cls, endpoints: List[BaseAPIEndpoint]) -> List[BaseAPIEndpoint]:
        unique: Dict[tuple[str, EndpointType, ApiType | None], BaseAPIEndpoint] = {}
        for endpoint in endpoints or []:
            uri = (endpoint.uri or "").strip()
            if not uri:
                continue
            key = (uri.lower(), endpoint.type, endpoint.api_type)
            if key not in unique:
                unique[key] = BaseAPIEndpoint(uri=uri, type=endpoint.type, api_type=endpoint.api_type)

        return sorted(
            unique.values(),
            key=lambda endpoint: (
                endpoint.uri.lower(),
                endpoint.api_type.value if endpoint.api_type is not None else "",
                0 if endpoint.type == EndpointType.CONSTANT else 1,
            ),
        )

    def is_empty(self) -> bool:
        """
        True when this chunk yielded no usable metadata.

        Operates on the flat per-chunk fields (apiType is detected separately and is not part
        of this model). The final payload has a different, grouped shape and its own emptiness
        check; see ``is_empty_info_result_payload``.
        """
        return not (
            self.name.strip()
            or (self.application_version or "").strip()
            or self.api_version.strip()
            or self.base_api_endpoint
            or self.database_name.strip()
        )


# TODO
# In the future, both of these will be calculated from signal agreement
DEFAULT_SCIM_AVAILABILITY_CONFIDENCE: float = 1.0
DEFAULT_REST_AVAILABILITY_CONFIDENCE: float = 1.0

# Maps free-form availability wording from documentation-free protocol signals to the
# canonical protocol availability. Shared by the signal schemas and any future consumer so
# the vocabulary stays in one place.
_PROTOCOL_AVAILABILITY_ALIASES: Dict[str, ProtocolAvailability] = {
    "available": ProtocolAvailability.AVAILABLE,
    "free": ProtocolAvailability.AVAILABLE,
    "included": ProtocolAvailability.AVAILABLE,
    "standard": ProtocolAvailability.AVAILABLE,
    "public": ProtocolAvailability.AVAILABLE,
    "paid": ProtocolAvailability.PAID,
    "gated": ProtocolAvailability.PAID,
    "premium": ProtocolAvailability.PAID,
    "enterprise": ProtocolAvailability.PAID,
    "business": ProtocolAvailability.PAID,
    "partner": ProtocolAvailability.PAID,
    "unknown": ProtocolAvailability.UNKNOWN,
}


def normalize_protocol_availability(value: Any) -> ProtocolAvailability:
    """Canonicalize a protocol-availability value, defaulting to UNKNOWN when unsure."""
    if isinstance(value, ProtocolAvailability):
        return value
    if not isinstance(value, str):
        return ProtocolAvailability.UNKNOWN
    return _PROTOCOL_AVAILABILITY_ALIASES.get(value.strip().lower(), ProtocolAvailability.UNKNOWN)


class RestAvailabilityInfo(_EndpointCarrier):
    """
    REST-specific connectivity info and availability advisory.

    Carries the base endpoint(s) classified as REST plus an advisory about whether the REST
    API is generally usable: a product may expose a REST/OpenAPI API yet gate it behind a
    paid/enterprise/partner plan. Aggregated from the documentation-free REST signals (LLM
    knowledge, web search) and the per-chunk documentation apiType. Always present on the
    final payload (empty/unknown when REST was not detected).
    """

    status: ProtocolAvailability = Field(
        default=ProtocolAvailability.UNKNOWN,
        description="REST availability: 'available', 'paid', or 'unknown'.",
    )
    required_plan: str = Field(
        default="",
        description="Plan/tier required when status is 'paid' (e.g. 'Enterprise'); empty when unknown.",
    )
    sources: List[DetectionSource] = Field(
        default_factory=list,
        description="Signals that confirmed REST: documentation, knowledge_of_llm, web_search.",
    )
    confidence: float = Field(
        default=DEFAULT_REST_AVAILABILITY_CONFIDENCE,
        ge=0.0,
        le=1.0,
        description="Confidence in [0, 1]. Placeholder default until derived from signal agreement.",
    )


class ScimAvailabilityInfo(_EndpointCarrier):
    """
    SCIM-specific connectivity info and advisory availability.

    Carries the SCIM base endpoint(s) plus an advisory about whether SCIM is generally
    usable: SCIM may exist for a product yet require a paid/enterprise plan the customer
    might not have. Aggregated from the documentation-free SCIM signals. Always present on
    the final payload (empty/unknown when SCIM was not detected).
    """

    status: ProtocolAvailability = Field(
        default=ProtocolAvailability.UNKNOWN,
        description="SCIM availability: 'available', 'paid', or 'unknown'.",
    )
    required_plan: str = Field(
        default="",
        description="Plan/tier required when status is 'paid' (e.g. 'Enterprise'); empty when unknown.",
    )
    sources: List[DetectionSource] = Field(
        default_factory=list,
        description="Signals that confirmed SCIM: scim_cloud, documentation, knowledge, web_search.",
    )
    confidence: float = Field(
        default=DEFAULT_SCIM_AVAILABILITY_CONFIDENCE,
        ge=0.0,
        le=1.0,
        description="Confidence in [0, 1]. Placeholder default until derived from signal agreement.",
    )


class SqlAvailabilityInfo(CamelCaseModel):
    """
    SQL-specific connectivity info: the target database/schema name.

    Always present on the final payload (empty when the integration is not SQL).
    """

    database_name: str = Field(
        default="",
        description="Database/schema name the connector must connect to. Populated only for SQL integrations.",
    )


class InfoMetadata(InfoMetadataBase):
    """
    Final high-level product and API metadata, including the detected ``apiType``.

    This is the stored/returned payload. ``apiType`` is filled by the dedicated detection
    pipeline and merged in by the service layer, not by chunk extraction. Connectivity
    details are grouped into protocol-specific availability blocks. All three blocks are
    always present; irrelevant blocks are returned empty rather than dropped, so the payload
    shape is stable regardless of the detected ``apiType``.
    """

    model_config = {"extra": "forbid"}

    api_type: List[ApiType] = Field(
        default_factory=list,
        description=(
            "API technology types. Allowed values: REST, SCIM, SQL. OpenAPI/Swagger should be normalized to REST."
        ),
    )
    rest_availability: RestAvailabilityInfo = Field(
        default_factory=RestAvailabilityInfo,
        description="REST connectivity info (base endpoints). Empty when REST is not detected.",
    )
    scim_availability: ScimAvailabilityInfo = Field(
        default_factory=ScimAvailabilityInfo,
        description="SCIM connectivity info and availability advisory. Empty/unknown when SCIM is not detected.",
    )
    sql_availability: SqlAvailabilityInfo = Field(
        default_factory=SqlAvailabilityInfo,
        description="SQL connectivity info (database name). Empty when the integration is not SQL.",
    )

    @field_validator("api_type", mode="before")
    @classmethod
    def _normalize_api_type(cls, value: Any) -> List[ApiType]:
        return normalize_api_type_values(value)


class ApiTypeResponse(CamelCaseModel):
    """
    Structured output for the standalone apiType detection LLM call.

    Runs as its own per-chunk extraction, separate from the generic info metadata
    extraction, and is merged into the final ``InfoMetadata.apiType``.
    """

    api_type: List[ApiType] = Field(
        default_factory=list,
        description="API technology types detected for this fragment. Allowed values: REST, SCIM, SQL.",
    )

    @field_validator("api_type", mode="before")
    @classmethod
    def _normalize_api_type(cls, value: Any) -> List[ApiType]:
        return normalize_api_type_values(value)


class ApiTypeSignalResult(CamelCaseModel):
    """
    Structured output shared by the documentation-free SCIM apiType signals
    (knowledge-based and web-search-based).

    Both signals answer the same questions about a single application name: whether it
    exposes SCIM, which integration protocol types it has, and whether SCIM is generally
    available or restricted to a paid/enterprise plan.
    """

    supports_scim: bool = Field(
        default=False,
        description="True only when the named application is known to expose a SCIM provisioning API.",
    )
    api_type: List[ApiType] = Field(
        default_factory=list,
        description="Integration protocol types the application is known to support. Allowed values: REST, SCIM, SQL.",
    )
    scim_availability: ProtocolAvailability = Field(
        default=ProtocolAvailability.UNKNOWN,
        description=(
            "Whether SCIM is generally available ('available'), restricted to a paid/enterprise tier ('paid'), "
            "or not determinable ('unknown'). Use 'unknown' when unsure."
        ),
    )
    required_plan: str = Field(
        default="",
        description="Plan/tier required for SCIM when it is paid (e.g. 'Enterprise Grid'); empty otherwise.",
    )

    @field_validator("api_type", mode="before")
    @classmethod
    def _normalize_api_type(cls, value: Any) -> List[ApiType]:
        return normalize_api_type_values(value)

    @field_validator("scim_availability", mode="before")
    @classmethod
    def _normalize_scim_availability(cls, value: Any) -> ProtocolAvailability:
        return normalize_protocol_availability(value)


class RestSignalResult(CamelCaseModel):
    """
    Structured output shared by the documentation-free REST apiType signals
    (knowledge-based and web-search-based).

    Both signals answer the same questions about a single application name: whether it
    exposes a REST/OpenAPI HTTP API usable for provisioning, and whether that API is
    generally available or restricted to a paid/enterprise/partner plan. This mirrors
    ``ApiTypeSignalResult`` for SCIM but is intentionally kept separate so the REST signal
    can evolve without touching SCIM detection.
    """

    supports_rest: bool = Field(
        default=False,
        description="True only when the named application is known to expose a REST/OpenAPI provisioning API.",
    )
    availability: ProtocolAvailability = Field(
        default=ProtocolAvailability.UNKNOWN,
        description=(
            "Whether the REST API is generally available ('available'), restricted to a paid/enterprise/partner "
            "tier ('paid'), or not determinable ('unknown'). Use 'unknown' when unsure."
        ),
    )
    required_plan: str = Field(
        default="",
        description="Plan/tier required for REST when it is paid (e.g. 'Enterprise'); empty otherwise.",
    )

    @field_validator("availability", mode="before")
    @classmethod
    def _normalize_availability(cls, value: Any) -> ProtocolAvailability:
        return normalize_protocol_availability(value)


class InfoExtractionResponse(CamelCaseModel):
    """
    Container for per-chunk info metadata extraction (apiType handled separately).
    """

    info_metadata: Optional[InfoMetadataExtraction] = Field(
        default=None,
        description="High-level application metadata if discovered in the documentation. Null when unavailable.",
    )


class InfoResponse(CamelCaseModel):
    """
    Container for high-level API metadata. Return null/empty fields when unknown.
    Use the alias 'InfoMetadata' for serialization if needed.
    """

    info_metadata: Optional[InfoMetadata] = Field(
        default=None,
        description="High-level application and API metadata if discovered in the documentations. Null when unavailable.",
    )
