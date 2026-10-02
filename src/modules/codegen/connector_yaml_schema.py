# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

"""Internal structural contracts for the documented REST, SCIM and SQL YAML union.

Absent keys preserve framework defaults; explicit null is only valid for bare
attributes and literal conditioned values. Script metadata controls syntax checking.
Nested unknown options are retained for advisory logging: this local model is
not an exhaustive contract for every connector runtime version. Closed value
vocabularies and keys the runtime rejects mirror the connector runtime parsers at
the revisions listed in docs/codegen-expert-references.adoc.
"""

import re
from collections.abc import Mapping
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

# conndev DeclConnIdTypeParser: case-sensitive Java simple names.
_ConnIdType = Literal[
    "String",
    "Integer",
    "Long",
    "Boolean",
    "Double",
    "Float",
    "Character",
    "Byte",
    "Binary",
    "BigDecimal",
    "BigInteger",
    "GuardedString",
    "GuardedByteArray",
    "ZonedDateTime",
    "Map",
    "ConnectorObjectReference",
    "EmbeddedObject",
]
# conndev JsonSchemaValueMapping: case-sensitive base JSON types.
_JsonType = Literal["string", "integer", "boolean", "number", "binary"]
# connector-scimrest HttpMethod, parsed case-insensitively.
_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"})
# conndev AttributeResolverBuilder.ResolutionType, parsed case-insensitively.
_RESOLUTION_TYPES = frozenset({"PER_OBJECT", "BATCH"})
# Recognize only simple complete paths. Slashy literals and $-prefixed Groovy
# identifiers must reach the embedded-script syntax check instead.
_BARE_EXTRACTOR_PATH = re.compile(r"(?:\$(?:\.\.?[\w*]+|\[(?:\d+|\*|'[^'\r\n]*'|\"[^\"\r\n]*\")\])*|(?:/[\w~-]+)+)")


def _require_case_insensitive_member(value: str, allowed: frozenset[str]) -> str:
    if value.upper() not in allowed:
        raise ValueError(f"expected one of {', '.join(sorted(allowed))} (case-insensitive)")
    return value


class _Configuration(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow")
    nullable_fields: ClassVar[frozenset[str]] = frozenset()
    # Keys the connector runtime rejects, mapped to the reason reported to the caller.
    unsupported_keys: ClassVar[Mapping[str, str]] = {}

    @model_validator(mode="before")
    @classmethod
    def reject_unsupported_keys(cls, data: Any) -> Any:
        if isinstance(data, Mapping):
            for key, reason in cls.unsupported_keys.items():
                if key in data:
                    raise ValueError(f"'{key}' is not supported in declarative YAML: {reason}")
        return data

    @field_validator("*", mode="before")
    @classmethod
    def reject_explicit_null(cls, value: Any, info: ValidationInfo) -> Any:
        if value is None and info.field_name not in cls.nullable_fields:
            raise ValueError("expected a configured value, not null")
        return value


def _script(*, expression: bool = False, empty_body: bool = False) -> Any:
    return Field(default=None, json_schema_extra={"script": True, "expression": expression, "empty_body": empty_body})


_GROOVY_ONLY_VALUE_MAPPING = "custom value mapping (implementation with deserialize/serialize) is Groovy-only"


class _AttributePath(_Configuration):
    type: str
    value: str

    @field_validator("type")
    @classmethod
    def validate_path_type(cls, value: str) -> str:
        if value.upper() not in {"JSON_PATH", "JSON_POINTER", "SCIM"}:
            raise ValueError("expected JSON_PATH, JSON_POINTER or SCIM")
        return value


class _ScimAttribute(_Configuration):
    unsupported_keys: ClassVar[Mapping[str, str]] = {"implementation": _GROOVY_ONLY_VALUE_MAPPING}

    name: str | None = Field(default=None)
    type: str | None = Field(default=None)
    path: str | _AttributePath | None = Field(default=None)


class _ConnIdAttribute(_Configuration):
    name: str | None = Field(default=None)
    type: _ConnIdType | None = Field(default=None)


class _JsonAttribute(_Configuration):
    unsupported_keys: ClassVar[Mapping[str, str]] = {"implementation": _GROOVY_ONLY_VALUE_MAPPING}

    name: str | None = Field(default=None)
    type: _JsonType | None = Field(default=None)
    openApiFormat: str | None = Field(default=None)
    path: str | _AttributePath | None = Field(default=None)


class _SqlAttribute(_Configuration):
    name: str | None = Field(default=None)
    type: str | None = Field(default=None)
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
    jsonType: _JsonType | None = Field(default=None)
    openApiFormat: str | None = Field(default=None)
    json_mapping: _JsonAttribute | None = Field(default=None, alias="json")
    connId: _ConnIdAttribute | None = Field(default=None)
    scim: _ScimAttribute | None = Field(default=None)
    sql: _SqlAttribute | None = Field(default=None)


class _Reference(_Configuration):
    objectClass: str | None = Field(default=None)
    role: str | None = Field(default=None)
    subtype: str | None = Field(default=None)


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


class _HttpEndpoint(_Configuration):
    path: str
    method: str | None = Field(default=None)

    @field_validator("method")
    @classmethod
    def validate_method(cls, value: str) -> str:
        return _require_case_insensitive_member(value, _HTTP_METHODS)


class _WriteEndpoint(_HttpEndpoint):
    unsupported_keys: ClassVar[Mapping[str, str]] = {
        "supportedAttributes": "only update endpoints accept it; limit create or delete attributes in Groovy"
    }

    request: _Request | None = Field(default=None)


class _UpdateEndpoint(_WriteEndpoint):
    unsupported_keys: ClassVar[Mapping[str, str]] = {}

    supportedAttributes: list[str | _SupportedAttribute] | None = Field(default=None)


class _ExtractorPath(_Configuration):
    type: str = "JSON_PATH"
    value: str

    @field_validator("type")
    @classmethod
    def validate_path_type(cls, value: str) -> str:
        if value.upper() not in {"JSON_PATH", "JSON_POINTER"}:
            raise ValueError("expected JSON_PATH or JSON_POINTER")
        return value


class _PagingParameter(_Configuration):
    location: Literal["query", "header", "body"] = Field(alias="in")
    name: str | None = Field(default=None)


class _PagingSupport(_Configuration):
    pageSize: int | None = Field(default=None, ge=1)
    parameters: dict[Literal["pageSize", "page", "offset"], _PagingParameter] | None = Field(default=None)


class _SearchEndpoint(_HttpEndpoint):
    responseFormat: Literal["JSON_ARRAY", "JSON_OBJECT"] | None = Field(default=None)
    objectExtractor: str | _ExtractorPath | None = _script()
    pagingSupport: str | _PagingSupport | None = _script()
    singleResult: bool | None = Field(default=None)
    emptyFilterSupported: bool | None = Field(default=None)
    supportedFilters: list[_Filter] | None = Field(default=None)

    @field_validator("objectExtractor")
    @classmethod
    def reject_path_written_as_script(cls, value: str | _ExtractorPath) -> str | _ExtractorPath:
        # A scalar is always compiled as a Groovy block, so `$.data` parses but fails at runtime.
        stripped = value.strip() if isinstance(value, str) else ""
        if _BARE_EXTRACTOR_PATH.fullmatch(stripped):
            raise ValueError(
                "a scalar objectExtractor is a Groovy block; write a JSONPath or JSON Pointer as {value: ...}"
            )
        return value


class _Operation(_Configuration):
    enabled: bool | None = Field(default=None)


class _WriteOperation(_Operation):
    endpoints: list[_WriteEndpoint] | None = Field(default=None)


class _UpdateOperation(_Operation):
    endpoints: list[_UpdateEndpoint] | None = Field(default=None)


class _AttributeResolver(_Configuration):
    attribute: str | None = Field(default=None)
    resolutionType: str | None = Field(default=None)
    implementation: str | None = _script()

    @field_validator("resolutionType")
    @classmethod
    def validate_resolution_type(cls, value: str) -> str:
        return _require_case_insensitive_member(value, _RESOLUTION_TYPES)


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


_NonBlankString = Annotated[str, Field(min_length=1, pattern=r"\S")]


class _ScimExtension(_Configuration):
    uri: _NonBlankString
    flatten: _NonBlankString | list[_NonBlankString] | None = Field(default=None)


class _ScimClass(_Configuration):
    schemaUri: str | None = Field(default=None)
    name: str | None = Field(default=None)
    onlyExplicitlyListed: bool | None = Field(default=None)
    flatten: _NonBlankString | list[_NonBlankString] | None = Field(default=None)
    extensions: dict[_NonBlankString, _NonBlankString | _ScimExtension] | None = Field(default=None)


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
    update: _UpdateOperation | None = Field(default=None)
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
    # Keep the document envelope strict to reject wrong artifact shapes.
    model_config = ConfigDict(strict=True, extra="forbid")
    unsupported_keys: ClassVar[Mapping[str, str]] = {
        "relationships": 'the connector rejects this block; declare relationships with a Groovy relationship("...") block'
    }

    objectClasses: dict[str, _ObjectClass] | None = Field(default=None)
    authentication: _Authentication | None = Field(default=None)
