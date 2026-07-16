# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCIM_SERVICE_PROVIDER_CONFIG_URN = "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"


class ScimSupportedFeature(BaseModel):
    """Boolean SCIM service-provider capability."""

    supported: bool


class ScimBulkFeature(ScimSupportedFeature):
    """SCIM bulk-operation capability and advertised request limits."""

    model_config = ConfigDict(populate_by_name=True)

    max_operations: Optional[int] = Field(
        default=None,
        ge=0,
        validation_alias="maxOperations",
        serialization_alias="maxOperations",
    )
    max_payload_size: Optional[int] = Field(
        default=None,
        ge=0,
        validation_alias="maxPayloadSize",
        serialization_alias="maxPayloadSize",
    )


class ScimFilterFeature(ScimSupportedFeature):
    """SCIM filtering capability and advertised response-size limit."""

    model_config = ConfigDict(populate_by_name=True)

    max_results: Optional[int] = Field(
        default=None,
        ge=0,
        validation_alias="maxResults",
        serialization_alias="maxResults",
    )


class ScimAuthenticationScheme(BaseModel):
    """Authentication scheme advertised by the SCIM service provider."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    description: Optional[str] = None
    spec_uri: Optional[str] = Field(
        default=None,
        validation_alias="specUri",
        serialization_alias="specUri",
    )
    documentation_uri: Optional[str] = Field(
        default=None,
        validation_alias="documentationUri",
        serialization_alias="documentationUri",
    )
    type: str
    primary: bool = False


class ScimServiceProviderMeta(BaseModel):
    """Relevant SCIM meta fields carried by the exported configuration resource."""

    model_config = ConfigDict(populate_by_name=True)

    resource_type: Optional[str] = Field(
        default=None,
        validation_alias="resourceType",
        serialization_alias="resourceType",
    )
    location: Optional[str] = None


class ScimServiceProviderConfig(BaseModel):
    """Validated SCIM ServiceProviderConfig contract exported by connector-development."""

    model_config = ConfigDict(populate_by_name=True)

    schemas: List[str]
    documentation_uri: Optional[str] = Field(
        default=None,
        validation_alias="documentationUri",
        serialization_alias="documentationUri",
    )
    patch: ScimSupportedFeature
    bulk: ScimBulkFeature
    filter: ScimFilterFeature
    change_password: ScimSupportedFeature = Field(
        validation_alias="changePassword",
        serialization_alias="changePassword",
    )
    sort: ScimSupportedFeature
    etag: ScimSupportedFeature
    authentication_schemes: List[ScimAuthenticationScheme] = Field(
        default_factory=list,
        validation_alias="authenticationSchemes",
        serialization_alias="authenticationSchemes",
    )
    meta: Optional[ScimServiceProviderMeta] = None

    @model_validator(mode="after")
    def _validate_schema_urn(self) -> "ScimServiceProviderConfig":
        normalized = {schema.strip().lower() for schema in self.schemas}
        if SCIM_SERVICE_PROVIDER_CONFIG_URN.lower() not in normalized:
            raise ValueError(f"schemas must contain {SCIM_SERVICE_PROVIDER_CONFIG_URN}")
        return self
