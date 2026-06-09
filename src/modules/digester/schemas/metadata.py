# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from src.common.enums import ApiType
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


class InfoMetadata(BaseModel):
    """
    High-level product and API metadata extracted from documentations.
    Focus on global application info, not per-endpoint details.
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
    api_type: List[ApiType] = Field(
        default_factory=list,
        validation_alias="apiType",
        serialization_alias="apiType",
        description=("API technology types. Allowed values: REST, SCIM. OpenAPI/Swagger should be normalized to REST."),
    )
    base_api_endpoint: List[BaseAPIEndpoint] = Field(
        default_factory=list,
        validation_alias="baseApiEndpoint",
        serialization_alias="baseApiEndpoint",
        description="One or more base endpoints/URI templates with their constant/dynamic classification.",
    )

    model_config = {"populate_by_name": True}

    @field_validator("api_type", mode="before")
    @classmethod
    def _normalize_api_type(cls, value: Any) -> List[ApiType]:
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
