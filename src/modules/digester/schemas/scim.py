# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import List, Optional

from pydantic import Field, model_validator

from src.core.schema import CamelCaseModel

SCIM_SERVICE_PROVIDER_CONFIG_URN = "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"


class ScimSupportedFeature(CamelCaseModel):
    """Boolean SCIM service-provider capability."""

    supported: bool


class ScimBulkFeature(ScimSupportedFeature):
    """SCIM bulk-operation capability and advertised request limits."""

    max_operations: Optional[int] = Field(
        default=None,
        ge=0,
    )
    max_payload_size: Optional[int] = Field(
        default=None,
        ge=0,
    )


class ScimFilterFeature(ScimSupportedFeature):
    """SCIM filtering capability and advertised response-size limit."""

    max_results: Optional[int] = Field(
        default=None,
        ge=0,
    )


class ScimAuthenticationScheme(CamelCaseModel):
    """Authentication scheme advertised by the SCIM service provider."""

    name: str
    description: Optional[str] = None
    spec_uri: Optional[str] = Field(
        default=None,
    )
    documentation_uri: Optional[str] = Field(
        default=None,
    )
    type: str
    primary: bool = False


class ScimServiceProviderMeta(CamelCaseModel):
    """Relevant SCIM meta fields carried by the exported configuration resource."""

    resource_type: Optional[str] = Field(
        default=None,
    )
    location: Optional[str] = None


class ScimServiceProviderConfig(CamelCaseModel):
    """Validated SCIM ServiceProviderConfig contract exported by connector-development."""

    schemas: List[str]
    documentation_uri: Optional[str] = Field(
        default=None,
    )
    patch: ScimSupportedFeature
    bulk: ScimBulkFeature
    filter: ScimFilterFeature
    change_password: ScimSupportedFeature = Field()
    sort: ScimSupportedFeature
    etag: ScimSupportedFeature
    authentication_schemes: List[ScimAuthenticationScheme] = Field(
        default_factory=list,
    )
    meta: Optional[ScimServiceProviderMeta] = None

    @model_validator(mode="after")
    def _validate_schema_urn(self) -> "ScimServiceProviderConfig":
        normalized = {schema.strip().lower() for schema in self.schemas}
        if SCIM_SERVICE_PROVIDER_CONFIG_URN.lower() not in normalized:
            raise ValueError(f"schemas must contain {SCIM_SERVICE_PROVIDER_CONFIG_URN}")
        return self
