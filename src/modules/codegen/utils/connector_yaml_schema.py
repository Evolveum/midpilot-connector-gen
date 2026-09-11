# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

"""Internal structural contracts for the documented REST, SCIM and SQL YAML union.

Absent keys preserve framework defaults; explicit null is only valid for bare
attributes and literal conditioned values. Script metadata controls syntax checking.
"""

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


class _Configuration(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    nullable_fields: ClassVar[frozenset[str]] = frozenset()

    @field_validator("*", mode="before")
    @classmethod
    def reject_explicit_null(cls, value: Any, info: ValidationInfo) -> Any:
        if value is None and info.field_name not in cls.nullable_fields:
            raise ValueError("expected a configured value, not null")
        return value


def _script(*, expression: bool = False, empty_body: bool = False) -> Any:
    return Field(default=None, json_schema_extra={"script": True, "expression": expression, "empty_body": empty_body})


class _ValueMapping(_Configuration):
    deserialize: str | None = _script()
    serialize: str | None = _script()


class _ScimAttribute(_Configuration):
    name: str | None = Field(default=None)
    type: str | None = Field(default=None)
    path: str | None = Field(default=None)
    implementation: _ValueMapping | None = Field(default=None)


class _ConnIdAttribute(_Configuration):
    name: str | None = Field(default=None)
    type: str | None = Field(default=None)


class _JsonAttribute(_ConnIdAttribute):
    openApiFormat: str | None = Field(default=None)


class _SqlAttribute(_ConnIdAttribute):
    column: str | None = Field(default=None)
    notNull: bool | None = Field(default=None)
    unique: bool | None = Field(default=None)
    primaryKey: bool | None = Field(default=None)
    autoIncrement: bool | None = Field(default=None)


class _Attribute(_Configuration):
    description: str | None = Field(default=None)
    required: bool | None = Field(default=None)
    multiValued: bool | None = Field(default=None)
    creatable: bool | None = Field(default=None)
    updateable: bool | None = Field(default=None)
    updatable: bool | None = Field(default=None)
    readable: bool | None = Field(default=None)
    returnedByDefault: bool | None = Field(default=None)
    emulated: bool | None = Field(default=None)
    complexType: str | None = Field(default=None)
    jsonType: str | None = Field(default=None)
    openApiFormat: str | None = Field(default=None)
    json_mapping: _JsonAttribute | None = Field(default=None, alias="json")
    connId: _ConnIdAttribute | None = Field(default=None)
    scim: _ScimAttribute | None = Field(default=None)
    sql: _SqlAttribute | None = Field(default=None)


class _Reference(_Configuration):
    objectClass: str | None = Field(default=None)
    role: str | None = Field(default=None)
    subtype: str | None = Field(default=None)


class _RelationshipResolver(_Configuration):
    resolution: str | None = Field(default=None)
    search: str | None = Field(default=None)
    implementation: str | None = _script()


class _RelationshipAttribute(_Configuration):
    name: str
    resolver: _RelationshipResolver | None = Field(default=None)


class _Participant(_Configuration):
    class_name: str = Field(alias="class")
    attribute: _RelationshipAttribute


class _Relationship(_Configuration):
    subject: _Participant
    object: _Participant


class _Request(_Configuration):
    contentType: str | None = Field(default=None)
    body: str | None = _script(empty_body=True)


class _Transition(_Configuration):
    nullable_fields = frozenset({"from_value", "to"})
    from_value: Any = Field(default=None, alias="from")
    to: Any = None


class _SupportedAttribute(_Configuration):
    nullable_fields = frozenset({"value"})
    name: str
    value: Any = None
    transition: _Transition | None = Field(default=None)


class _Filter(_Configuration):
    spec: str | None = _script(expression=True)
    request: str | None = _script()


class _WriteEndpoint(_Configuration):
    path: str
    method: str | None = Field(default=None)
    request: _Request | None = Field(default=None)
    supportedAttributes: list[str | _SupportedAttribute] | None = Field(default=None)


class _SearchEndpoint(_Configuration):
    path: str
    method: str | None = Field(default=None)
    responseFormat: str | None = Field(default=None)
    objectExtractor: str | None = _script()
    pagingSupport: str | None = _script()
    singleResult: bool | None = Field(default=None)
    emptyFilterSupported: bool | None = Field(default=None)
    supportedFilters: list[_Filter] | None = Field(default=None)


class _WriteOperation(_Configuration):
    enabled: bool | None = Field(default=None)
    endpoints: list[_WriteEndpoint] | None = Field(default=None)


class _AttributeResolver(_Configuration):
    attribute: str | None = Field(default=None)
    resolutionType: str | None = Field(default=None)
    implementation: str | None = _script()


class _Normalize(_Configuration):
    toSingleValue: str | None = Field(default=None)
    rewriteUid: str | None = _script()
    rewriteName: str | None = _script()
    restoreUid: str | None = _script()
    restoreName: str | None = _script()


class _CustomSearch(_Configuration):
    implementation: str | None = _script()
    supportedFilters: list[_Filter] | None = Field(default=None)
    emptyFilterSupported: bool | None = Field(default=None)


class _Search(_Configuration):
    endpoints: list[_SearchEndpoint] | None = Field(default=None)
    normalize: _Normalize | None = Field(default=None)
    attributeResolvers: list[_AttributeResolver] | None = Field(default=None)
    custom: _CustomSearch | None = Field(default=None)


class _ScimClass(_Configuration):
    schemaUri: str | None = Field(default=None)
    name: str | None = Field(default=None)
    onlyExplicitlyListed: bool | None = Field(default=None)
    extensions: dict[str, str] | None = Field(default=None)


class _SqlClass(_Configuration):
    table: str | None = Field(default=None)
    schema_name: str | None = Field(default=None, alias="schema")


class _ObjectClass(_Configuration):
    description: str | None = Field(default=None)
    embedded: bool | None = Field(default=None)
    readOnly: bool | None = Field(default=None)
    onlyExplicitlyListed: bool | None = Field(default=None)
    connId: dict[str, str] | None = Field(default=None)
    scim: _ScimClass | None = Field(default=None)
    sql: _SqlClass | None = Field(default=None)
    attributes: dict[str, _Attribute | None] | None = Field(default=None)
    references: dict[str, _Reference] | None = Field(default=None)
    create: _WriteOperation | None = Field(default=None)
    update: _WriteOperation | None = Field(default=None)
    delete: _WriteOperation | None = Field(default=None)
    search: _Search | None = Field(default=None)


class _AuthenticationMethod(_Configuration):
    implementation: str | None = _script()


class _OAuthMethod(_AuthenticationMethod):
    validateToken: str | None = _script()
    buildTokenRequest: str | None = _script()
    parseTokenResponse: str | None = _script()
    applyToken: str | None = _script()
    onResponse: str | None = _script()


class _AuthenticationChannel(_Configuration):
    basic: _AuthenticationMethod | None = Field(default=None)
    bearer: _AuthenticationMethod | None = Field(default=None)
    jwtBearer: _AuthenticationMethod | None = Field(default=None)
    apiKey: _AuthenticationMethod | None = Field(default=None)
    oauth2ClientCredentials: _OAuthMethod | None = Field(default=None)
    oauth2Password: _OAuthMethod | None = Field(default=None)
    oauth2JwtBearer: _OAuthMethod | None = Field(default=None)
    oauth2Saml: _OAuthMethod | None = Field(default=None)
    preference: list[str] | None = Field(default=None)


class _Authentication(_Configuration):
    rest: _AuthenticationChannel | None = Field(default=None)
    scim: _AuthenticationChannel | None = Field(default=None)


class ConnectorYamlDocument(_Configuration):
    objectClasses: dict[str, _ObjectClass] | None = Field(default=None)
    relationships: dict[str, _Relationship] | None = Field(default=None)
    authentication: _Authentication | None = Field(default=None)
