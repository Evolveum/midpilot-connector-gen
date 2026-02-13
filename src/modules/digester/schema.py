# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_serializer


# --- Object Classes ---
class ObjectClass(BaseModel):
    """
    IGA/IDM domain object class as extracted from API schemas.
    The model guides the LLM to return only first-class identity/access concepts
    (e.g., User, Group, Role, Permission, Organization, Membership/Assignment, Credential,
    Attachment, Attribute/FieldDefinition) and to annotate basic taxonomy metadata.
    """

    model_config = {
        "json_encoders": {
            # Custom JSON encoder to exclude None values
            type(None): lambda _: None,
        },
        "json_schema_extra": {
            "exclude_none": True,  # Exclude None values from JSON output
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
    relevant: Literal["true", "false", "maybe"] = Field(
        default="true",
        description=(
            "Indicates whether this object class is relevant to the IDM/IGA domain. "
            "All extracted object classes SHOULD already be filtered for relevance, "
            "so this value is typically 'true'."
        ),
    )
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
    description: str = Field(
        description=(
            "A brief explanation of what this class represents in the system. "
            "Include key characteristics and usage context. Keep it concise (1-2 sentences)."
        ),
    )
    relevant_chunks: List[Dict[str, UUID]] = Field(
        default_factory=list,
        validation_alias="relevantChunks",
        serialization_alias="relevantChunks",
        json_schema_extra={"exclude": True},
        description=(
            "List of chunks that contain relevant information about this object class. "
            "Each entry contains only 'docUuid' (the document UUID is the chunk identifier). "
            "This field is populated automatically by the system and should NOT be filled by the LLM."
        ),
    )
    # These fields will be excluded from JSON when None
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

    @field_validator("relevant_chunks", mode="before")
    @classmethod
    def validate_relevant_chunks(cls, v: Any) -> List[Dict[str, UUID]]:
        if not isinstance(v, list):
            return []

        validated_chunks: List[Dict[str, UUID]] = []
        for chunk in v:
            if not isinstance(chunk, dict):
                continue
            if "docUuid" in chunk:
                validated_chunks.append({"docUuid": chunk["docUuid"]})

        return validated_chunks


class ObjectClassesResponse(BaseModel):
    """
    Container for extracted IGA/IDM object classes. Use the alias 'objectClasses' in output.
    Return an empty list when none are present in the chunk.
    """

    # Primary Python attribute in snake_case for convenient access in code/tests
    object_classes: List[ObjectClass] = Field(
        default_factory=list,
        validation_alias="objectClasses",
        serialization_alias="objectClasses",
        description=(
            "List of extracted IGA/IDM-relevant object classes. Use the alias 'objectClasses' in the final JSON."
        ),
    )

    model_config = {"populate_by_name": True}

    # Backward-compatible camelCase property used throughout prompts/utils
    @property
    def objectClasses(self) -> List[ObjectClass]:  # pragma: no cover - simple alias
        return self.object_classes


class ObjectClassRelevancyItem(BaseModel):
    """
    Item representing an object class as string with its relevancy status.
    """

    name: str = Field(
        ...,
        description="Exact name of the object class as it appears in the documentation.",
    )
    relevant: Literal["low", "medium", "high"] = Field(
        ...,
        description=(
            "Relevance of this class to IGA/IDM use-cases. Use 'high' for mission-critical domain entities, "
            "'medium' for important but not central classes, and 'low' for peripheral or non-domain types."
        ),
    )


class ObjectClassesRelevancyResponse(BaseModel):
    """
    Container for filtered IGA/IDM object classes based on relevancy.
    """

    objectClasses: List[ObjectClassRelevancyItem] = Field(
        default_factory=list,
        description=("List of ObjectClassRelevancyItem representing object classes with their relevancy status."),
    )


# --- Object Classes ---


# --- Auth ---
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
    type: str = Field(
        ...,
        description=(
            "Normalized auth type when obvious. Common values: 'basic', 'bearer', 'session', 'oauth2', "
            "'apiKey', 'mtls'. If unclear, use the closest descriptive string from the docs."
        ),
    )
    quirks: Optional[str] = Field(
        default="",
        description=(
            "Short, verbatim notes about special behavior or non-standard aspects (e.g., header/cookie/name, "
            "required scopes/realms, token prefix, custom challenge/flow). Leave empty if not applicable."
        ),
    )


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
    type: Literal["constant", "dynamic"] = Field(
        ..., description="'constant' if same for all deployments; 'dynamic' if varies per tenant/installation."
    )

    model_config = {"populate_by_name": True}


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
    api_type: List[str] = Field(
        default_factory=list,
        validation_alias="apiType",
        serialization_alias="apiType",
        description="API technology types (e.g., REST, OpenAPI, SCIM, SOAP, GraphQL, Other).",
    )
    base_api_endpoint: List[BaseAPIEndpoint] = Field(
        default_factory=list,
        validation_alias="baseApiEndpoint",
        serialization_alias="baseApiEndpoint",
        description="One or more base endpoints/URI templates with their constant/dynamic classification.",
    )

    model_config = {"populate_by_name": True}


class InfoResponse(BaseModel):
    """
    Container for high-level API metadata. Return null/empty fields when unknown.
    Use the alias 'infoAboutSchema' for serialization if needed.
    """

    info_about_schema: InfoMetadata = Field(
        default_factory=InfoMetadata,
        validation_alias="infoAboutSchema",
        serialization_alias="infoAboutSchema",
        description="High-level application and API metadata if discovered in the documentations.",
    )

    model_config = {"populate_by_name": True}

    # Accept null incoming payloads by converting them to an empty object
    @field_validator("info_about_schema", mode="before")
    @classmethod
    def _normalize_info(cls, v):
        if v is None:
            return {}
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


# --- Endpoints ---
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
    method: str = Field(
        ...,
        description="HTTP method in uppercase (e.g., GET, POST, PUT, PATCH, DELETE).",
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
        description="Primary response media type if specified (e.g., 'application/json', 'application/hal+json').",
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
        description="Primary response media type if specified (e.g., 'application/json', 'application/hal+json').",
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
