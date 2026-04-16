# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_serializer, field_validator, model_serializer

from src.common.enums import ApiType
from src.modules.digester.enums import (
    AuthType,
    ConfidenceLevel,
    EndpointMethod,
    EndpointType,
    RelevantLevel,
)

# --- Object Classes ---


class BaseObjectClass(BaseModel):
    """
    Minimal shared representation.
    Used when only identity + short meaning of the class are needed.
    """

    model_config = {
        "json_schema_extra": {
            "exclude_none": True,
        },
        "populate_by_name": True,
    }

    name: str = Field(
        ...,
        description=(
            "Exact type/object name as it appears in the documentations (preserve original casing). "
            "This should be a primary domain concept or a first-class link between such concepts."
        ),
    )
    description: str = Field(
        description=(
            "A brief explanation of what this class represents in the system. "
            "Include key characteristics and usage context. Keep it concise (1-2 sentences)."
        ),
    )


class ExtendedObjectClass(BaseObjectClass):
    """
    First-pass extraction model.
    Extends base information with structural metadata from the schema.
    """

    superclass: Optional[str] = Field(
        default=None,
        description=(
            "Name of the immediate parent type if the documentation shows clear inheritance "
            "or extension. "
            "Extract only when this relationship is explicitly or structurally defined, for example:\n"
            "  - 'extends Foo', 'inherits from Bar'\n"
            "  - OpenAPI allOf referencing another schema\n"
            "  - A discriminator mapping where this type is the documented supertype\n"
            "Use the exact superclass name as it appears in the docs (preserve casing). "
            "Leave null if no inheritance relationship is clearly documented. "
            "Example: If docs show 'AdminUser extends User', set superclass='User'."
        ),
    )
    abstract: Optional[bool] = Field(
        default=None,
        description=(
            "Whether the type is abstract (not directly instantiated). Set true only if the docs explicitly "
            "call it abstract/base/union/interface not intended for direct instances. "
            "Leave false if not applicable."
        ),
    )
    embedded: Optional[bool] = Field(
        default=None,
        description=(
            "Whether the type is used only as an embedded/inline component of other objects and is not a "
            "standalone manageable resource (no independent CRUD/identifier). "
            "Leave false if not applicable."
        ),
    )


class ObjectClassWithConfidence(BaseObjectClass):
    """
    Second-pass enrichment model.
    Contains only base data + confidence from classification.
    """

    confidence: ConfidenceLevel = Field(
        ...,
        description=(
            "Reliability/confidence for IGA/IDM relevance assigned in dedicated enrichment. "
            "Allowed values: low, medium, high."
        ),
    )


class RankedObjectClass(ExtendedObjectClass):
    """
    Third-pass sorting model.
    Contains all ranking-relevant fields, excluding endpoints/attributes/chunk references.
    """

    relevant: RelevantLevel = Field(
        default=RelevantLevel.TRUE,
        description="IGA/IDM relevance marker for the final payload.",
    )
    confidence: ConfidenceLevel = Field(
        ...,
        description="Reliability/confidence level for IGA/IDM relevance (low/medium/high).",
    )


class FinalObjectClass(RankedObjectClass):
    """
    Final user-facing object class model.
    Adds system-populated fields not used in LLM ranking prompts.
    """

    relevant_documentations: List[Dict[str, str]] = Field(
        default_factory=list,
        validation_alias="relevantDocumentations",
        serialization_alias="relevantDocumentations",
        description=(
            "List of chunks that contain relevant information about this object class. "
            "Each entry is serialized as 'docId' and 'chunkId' UUID strings."
        ),
    )
    endpoints: Optional[List[Any]] = Field(
        default=None,
        exclude=True,
        description="List of endpoints associated with this object class. Only present when explicitly extracted.",
    )
    attributes: Optional[Dict[str, Any]] = Field(
        default=None,
        exclude=True,
        description="Dictionary of attributes for this object class. Only present when explicitly extracted.",
    )

    @field_validator("relevant_documentations", mode="before")
    @classmethod
    def validate_relevant_documentations(cls, v: Any) -> List[Dict[str, str]]:
        if not isinstance(v, list):
            return []

        validated_chunks: List[Dict[str, str]] = []
        for chunk in v:
            if not isinstance(chunk, dict):
                continue
            chunk_id = chunk.get("chunk_id") or chunk.get("chunkId")
            doc_id = chunk.get("doc_id") or chunk.get("docId")
            if chunk_id and doc_id:
                validated_chunks.append(
                    {
                        "chunk_id": str(chunk_id),
                        "doc_id": str(doc_id),
                    }
                )

        return validated_chunks

    @field_serializer("relevant_documentations", when_used="always")
    def serialize_relevant_documentations(self, value: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Expose relevantDocumentations in camelCase while keeping internal snake_case."""
        serialized: List[Dict[str, str]] = []
        for chunk in value or []:
            doc_id = chunk.get("doc_id") or chunk.get("docId")
            chunk_id = chunk.get("chunk_id") or chunk.get("chunkId")
            if not doc_id or not chunk_id:
                continue
            serialized.append({"docId": str(doc_id), "chunkId": str(chunk_id)})
        return serialized


class ObjectClassesResponse(BaseModel):
    """
    Final object classes returned to API consumers.
    """

    object_classes: List[FinalObjectClass] = Field(
        default_factory=list,
        validation_alias="objectClasses",
        serialization_alias="objectClasses",
        description=(
            "List of extracted object classes enriched with confidence and returned in final order. "
            "Use alias 'objectClasses' in JSON payloads."
        ),
    )

    model_config = {"populate_by_name": True}

    @property
    def objectClasses(self) -> List[FinalObjectClass]:
        return self.object_classes


class ObjectClassesExtendedResponse(BaseModel):
    """
    First LLM call response container.
    """

    object_classes: List[ExtendedObjectClass] = Field(
        default_factory=list,
        validation_alias="objectClasses",
        serialization_alias="objectClasses",
        description=(
            "List of extracted extended object classes from the first pass. Use alias 'objectClasses' in JSON payloads."
        ),
    )

    model_config = {"populate_by_name": True}

    @property
    def objectClasses(self) -> List[ExtendedObjectClass]:
        return self.object_classes


class ObjectClassesConfidenceResponse(BaseModel):
    """
    Second LLM call response container.
    """

    object_classes: List[ObjectClassWithConfidence] = Field(
        default_factory=list,
        validation_alias="objectClasses",
        serialization_alias="objectClasses",
        description="List of object classes with assigned confidence levels.",
    )

    model_config = {"populate_by_name": True}

    @property
    def objectClasses(self) -> List[ObjectClassWithConfidence]:
        return self.object_classes


class ObjectClassesRankedResponse(BaseModel):
    """
    Third LLM call response container.
    """

    object_classes: List[RankedObjectClass] = Field(
        default_factory=list,
        validation_alias="objectClasses",
        serialization_alias="objectClasses",
        description=(
            "Reordered list of ranked object classes. "
            "Each item includes fields needed for ranking and final output composition."
        ),
    )

    model_config = {"populate_by_name": True}

    @property
    def objectClasses(self) -> List[RankedObjectClass]:
        return self.object_classes


# --- Object Classes ---


class AuthInfo(BaseModel):
    """
    Authentication mechanism discovered in the API documentations/security schemes.
    Guide the LLM to extract concrete auth methods (e.g., Basic, Bearer/JWT, Session/Cookie,
    OAuth2 variants, API Key, mTLS) and capture notable non-standard behavior in quirks.
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
            "Normalized auth type. Allowed values: 'basic', 'bearer', 'oauth2', 'apiKey', "
            "'session', 'digest', 'mtls', 'openidConnect', 'other'."
        ),
    )
    quirks: Optional[str] = Field(
        default="",
        description=(
            "Short, verbatim notes about special behavior or non-standard aspects (e.g., header/cookie/name, "
            "required scopes/realms, token prefix, custom challenge/flow). Leave empty if not applicable."
        ),
    )

    @field_validator("type", mode="before")
    @classmethod
    def _normalize_auth_type(cls, value: Any) -> AuthType:
        """
        Normalize auth type variations to a stable, closed vocabulary.
        """
        if not isinstance(value, str):
            return AuthType.OTHER

        normalized = re.sub(r"[^a-z0-9]+", "", value.strip().lower())

        aliases = {
            # basic
            "basic": AuthType.BASIC,
            "basicauth": AuthType.BASIC,
            "httpbasic": AuthType.BASIC,
            # bearer
            "bearer": AuthType.BEARER,
            "jwt": AuthType.BEARER,
            "token": AuthType.BEARER,
            "accesstoken": AuthType.BEARER,
            "personalaccesstoken": AuthType.BEARER,
            "pat": AuthType.BEARER,
            # oauth2
            "oauth": AuthType.OAUTH2,
            "oauth2": AuthType.OAUTH2,
            "oauth2.0": AuthType.OAUTH2,
            "oauth 2.0": AuthType.OAUTH2,
            "oauth20": AuthType.OAUTH2,
            "authorizationcode": AuthType.OAUTH2,
            "clientcredentials": AuthType.OAUTH2,
            "devicecode": AuthType.OAUTH2,
            "pkce": AuthType.OAUTH2,
            # api key
            "apikey": AuthType.API_KEY,
            "api_key": AuthType.API_KEY,
            "apikeyauth": AuthType.API_KEY,
            "xapikey": AuthType.API_KEY,
            # session
            "session": AuthType.SESSION,
            "cookie": AuthType.SESSION,
            "cookiesession": AuthType.SESSION,
            "sessioncookie": AuthType.SESSION,
            # digest
            "digest": AuthType.DIGEST,
            "httpdigest": AuthType.DIGEST,
            # mtls
            "mtls": AuthType.MTLS,
            "mutualtls": AuthType.MTLS,
            "clientcertificate": AuthType.MTLS,
            # openid connect
            "openidconnect": AuthType.OPENID_CONNECT,
            "oidc": AuthType.OPENID_CONNECT,
            "openid": AuthType.OPENID_CONNECT,
            # fallback bucket
            "other": AuthType.OTHER,
            "custom": AuthType.OTHER,
        }

        return aliases.get(normalized, AuthType.OTHER)


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


# --- Auth ---


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


# --- Info about schema ---


# --- Attributes ---
class AttributeInfo(BaseModel):
    """
    Attribute metadata for an object class property as described in OpenAPI/JSON Schema.
    """

    type: Optional[str] = Field(
        default=None,
        description=(
            "Type as declared in the documentation (prefer OpenAPI). For simple attributes, use one of: 'string' "
            "(includes binaries encoded as base64), 'number', 'integer', or 'boolean'. For complex attributes, use "
            "the object class name. Put additional type details in 'format' (e.g., 'email', 'binary', 'double', "
            "'embedded', 'reference'). If a complex attribute is relevant, ensure the referenced object class is "
            "included in extracted object classes. Use null if unknown."
        ),
    )
    format: Optional[str] = Field(
        default=None,
        description=(
            "Format of the type with additional detail. For simple attributes, use an OpenAPI format registry value "
            "(e.g., 'email', 'uri', 'int64', 'date-time'). For complex attributes, use one of: 'embedded' or "
            "'reference'. Use 'embedded' for object classes directly embedded in JSON/XML. Use 'reference' for a "
            "reference to another full object class (embedded=false), even if the full object appears embedded in "
            "the payload. Use null if unknown."
        ),
    )
    description: Optional[str] = Field(
        default=None,
        description="Short description of attribute copied from documentation. Property description from the schema; null if not provided.",
    )
    mandatory: Optional[bool] = Field(
        default=None,
        description="Is attribute required? True if the attribute is required; otherwise false. Use null if unknown.",
    )
    updatable: Optional[bool] = Field(
        default=None,
        description="Can be attribute modified? False if readOnly=true; otherwise true. Use null if unknown.",
    )
    creatable: Optional[bool] = Field(
        default=None,
        description="Can attribute be used during create operation? False if readOnly=true; otherwise true (do not infer from endpoints). Use null if unknown.",
    )
    readable: Optional[bool] = Field(
        default=None,
        description="Is attribute readable? False if writeOnly=true; otherwise true. Use null if unknown.",
    )
    multivalue: Optional[bool] = Field(
        default=None,
        description="Is attribute multivalue? True if the property's type is 'array'; otherwise false. Use null if unknown.",
    )
    returnedByDefault: Optional[bool] = Field(
        default=None,
        description=(
            "Is attribute returned by default? Eg. attributes which requires fetching additional endpoint to resolve should."
            "True if the attribute is returned by default without additional calls; set false when it "
            "requires extra expansion or separate endpoint fetches. Use null if unknown."
        ),
    )
    scimAttribute: Optional[str] = Field(
        default=None,
        description=(
            "For SCIM mapping scenarios, the source SCIM attribute/path that maps to this application attribute "
            "(e.g., 'userName', 'emails[0].value', 'profile.startDate'). Leave null when not applicable."
        ),
    )
    relevant_documentations: List[Dict[str, str]] = Field(
        default_factory=list,
        validation_alias="relevantDocumentations",
        serialization_alias="relevantDocumentations",
        description=(
            "List of chunks that contain evidence for this specific attribute. "
            "Each entry is serialized as 'docId' and 'chunkId' UUID strings. "
            "This field is populated automatically by the system and should NOT be filled by the LLM."
        ),
    )

    @field_validator("relevant_documentations", mode="before")
    @classmethod
    def _validate_relevant_documentations(cls, v: Any) -> List[Dict[str, str]]:
        if not isinstance(v, list):
            return []

        validated_chunks: List[Dict[str, str]] = []
        for chunk in v:
            if not isinstance(chunk, dict):
                continue
            chunk_id = chunk.get("chunk_id") or chunk.get("chunkId")
            doc_id = chunk.get("doc_id") or chunk.get("docId")
            if chunk_id and doc_id:
                validated_chunks.append(
                    {
                        "chunk_id": str(chunk_id),
                        "doc_id": str(doc_id),
                    }
                )
        return validated_chunks

    @field_serializer("relevant_documentations", when_used="always")
    def _serialize_relevant_documentations(self, value: List[Dict[str, str]]) -> List[Dict[str, str]]:
        serialized: List[Dict[str, str]] = []
        for chunk in value or []:
            doc_id = chunk.get("doc_id") or chunk.get("docId")
            chunk_id = chunk.get("chunk_id") or chunk.get("chunkId")
            if not doc_id or not chunk_id:
                continue
            serialized.append({"docId": str(doc_id), "chunkId": str(chunk_id)})
        return serialized


class AttributeResponse(BaseModel):
    """
    Attribute map for a specific object class where each key is the property name.
    Return an empty map when the object class has no properties in the fragment.
    """

    attributes: Dict[str, AttributeInfo] = Field(
        default_factory=dict,
        description="Map of attribute name to its normalized metadata (AttributeInfo).",
    )


# --- Attributes ---


class EndpointInfo(BaseModel):
    """
    HTTP endpoint associated with a specific object class. Focus on endpoints that
    represent or manipulate the given class (CRUD, lifecycle, membership operations).
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
    suggested_use: List[str] = Field(
        default_factory=list,
        validation_alias="suggestedUse",
        serialization_alias="suggestedUse",
        description="List of endpoint suggested use-cases (e.g., 'create', 'update', 'delete', 'getById', 'getAll' 'search', 'activate', 'deactivate'). If unsure, leave empty.",
    )
    relevant_documentations: List[Dict[str, str]] = Field(
        default_factory=list,
        validation_alias="relevantDocumentations",
        serialization_alias="relevantDocumentations",
        description=(
            "List of chunks that contain evidence for this specific endpoint. "
            "Each entry is serialized as 'docId' and 'chunkId' UUID strings. "
            "This field is populated automatically by the system and should NOT be filled by the LLM."
        ),
    )

    @field_validator("relevant_documentations", mode="before")
    @classmethod
    def _validate_relevant_documentations(cls, v: Any) -> List[Dict[str, str]]:
        if not isinstance(v, list):
            return []

        validated_chunks: List[Dict[str, str]] = []
        for chunk in v:
            if not isinstance(chunk, dict):
                continue
            chunk_id = chunk.get("chunk_id") or chunk.get("chunkId")
            doc_id = chunk.get("doc_id") or chunk.get("docId")
            if chunk_id and doc_id:
                validated_chunks.append(
                    {
                        "chunk_id": str(chunk_id),
                        "doc_id": str(doc_id),
                    }
                )
        return validated_chunks

    @field_serializer("relevant_documentations", when_used="always")
    def _serialize_relevant_documentations(self, value: List[Dict[str, str]]) -> List[Dict[str, str]]:
        serialized: List[Dict[str, str]] = []
        for chunk in value or []:
            doc_id = chunk.get("doc_id") or chunk.get("docId")
            chunk_id = chunk.get("chunk_id") or chunk.get("chunkId")
            if not doc_id or not chunk_id:
                continue
            serialized.append({"docId": str(doc_id), "chunkId": str(chunk_id)})
        return serialized

    @field_validator("method", mode="before")
    @classmethod
    def _normalize_method(cls, value: Any) -> Any:
        """Accept lowercase/mixed-case methods and normalize them before literal validation."""
        if isinstance(value, str):
            return value.strip().upper()
        return value


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
    suggested_use: List[str] = Field(
        default_factory=list,
        validation_alias="suggestedUse",
        serialization_alias="suggestedUse",
        description="List of endpoint suggested use-cases (e.g., 'create', 'update', 'delete', 'getById', 'getAll' 'search', 'activate', 'deactivate'). If unsure, leave empty.",
    )


class EndpointResponse(BaseModel):
    """
    Container for endpoints discovered for a given object class. Return an empty list when none.
    """

    endpoints: List[EndpointInfo] = Field(
        default_factory=list,
        description="List of HTTP endpoints related to the specified object class.",
    )


# --- Endpoints ---


# --- Relation ---


class RelationRecord(BaseModel):
    """
    Relationship between two object classes discovered in the schema.
    """

    model_config = {"populate_by_name": True}

    name: str = Field(
        ...,
        description="Human-readable name of the relation. ALWAYS provide a meaningful name based on the relationship (e.g., 'User to Group', 'Account to User', etc.). Never leave empty.",
    )
    short_description: str = Field(
        default="",
        validation_alias="shortDescription",
        serialization_alias="shortDescription",
        description="Short description or summary if present. LLM can propose if documentation do not has description",
    )
    subject: str = Field(
        ...,
        description=(
            "Normalized lowercase name of the subject class (owner of the attribute). Must refer to a relevant class."
        ),
    )
    subject_attribute: Optional[str] = Field(
        default="",
        validation_alias="subjectAttribute",
        serialization_alias="subjectAttribute",
        description="Exact property name on the subject that establishes the relation (raw as in schema).",
    )
    object: str = Field(
        ...,
        description="Normalized lowercase name of the object class being referenced by the subject's property.",
    )
    object_attribute: Optional[str] = Field(
        default="",
        validation_alias="objectAttribute",
        serialization_alias="objectAttribute",
        description="Exact back-reference property name on the object class if explicitly documented; else empty.",
    )


class RelationsResponse(BaseModel):
    """
    Container for relation records extracted from a schema fragment. Return an empty list when none qualify.
    """

    relations: List[RelationRecord] = Field(
        default_factory=list,
        description="List of discovered relations with normalized class names and supporting fields.",
    )


# --- End Relation ---
