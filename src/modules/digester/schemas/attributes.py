# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from src.modules.digester.schemas.common import (
    DocProcessingSequenceItem,
    DocSequenceItem,
    DocSequenceMarker,
    RelevantDocumentationsMixin,
)

# --- Attributes ---

_UNDETERMINED = (
    "Null only when the documentation of this object class neither states nor lets you derive the value; never guess."
)
_COLLECTION_LINK_NOT_WRITABLE = (
    "A single link to a separate collection or sub-resource (e.g. a URL from which the related items are listed) "
    "is neither creatable nor updatable unless the documentation describes it as settable."
)
_TYPE_DESCRIPTION = (
    "Type as declared in the documentation (prefer OpenAPI). For a simple attribute use 'string' (including "
    "binaries encoded as base64), 'number', 'integer' or 'boolean'. For a complex attribute use the name of the "
    "object class as the documentation names the objects (e.g. 'Group'), not the name of a schema component, "
    "wrapper or transfer object (e.g. not 'GroupModel', 'GroupDto' or 'GroupCollection'); for a collection use the "
    "class of its items. Use 'object' only for an anonymous inline object. For an array use the type of its items; "
    "multiplicity belongs to multivalue. " + _UNDETERMINED
)
_FORMAT_DESCRIPTION = (
    "Additional type detail. For a simple attribute use an OpenAPI format registry value (e.g. 'email', 'uri', "
    "'int64', 'date-time'). For a complex attribute use 'reference' when it points to another full object class, "
    "even if the full object appears embedded in the payload, or 'embedded' when the object is part of this one. "
    "For an array use the format of its items. Do not put other standards or patterns (e.g. the name of a code "
    "standard or a regular expression) in format; leave it null and keep them in the description. Null when the "
    "documentation gives no format."
)
_MANDATORY_DESCRIPTION = (
    "Is the attribute mandatory for this object class in the IGA schema and in the connector? True when the "
    "documentation shows that an object cannot exist or be created without it (e.g. marked as required, required "
    "in this object class's create request, or described as mandatory). False when the documentation shows it as "
    "optional (e.g. it may be omitted, has a default value, or is marked as not required). " + _UNDETERMINED
)
_UPDATABLE_DESCRIPTION = (
    "Can the attribute be changed on an existing object? True when the documentation shows it as writable (e.g. "
    "write access in a field table) or it is accepted by this object class's update request. False when it is "
    "read-only or server-generated, explicitly not modifiable after creation, or missing from a documented update "
    "request of this object class. " + _COLLECTION_LINK_NOT_WRITABLE + " " + _UNDETERMINED
)
_CREATABLE_DESCRIPTION = (
    "Can the attribute be set when creating the object? True when the documentation shows it as writable on "
    "create (e.g. write access in a field table) or it is accepted by this object class's create request. False "
    "when it is read-only or server-generated, limited to existing objects (e.g. only on update), or missing from "
    "a documented create request of this object class. Do not decide it from the list of endpoints alone. "
    + _COLLECTION_LINK_NOT_WRITABLE
    + " "
    + _UNDETERMINED
)
_READABLE_DESCRIPTION = (
    "Can the attribute value be read from the target system? True when the documentation shows it as readable "
    "(read access, part of a returned representation). False when it is write-only: marked write-only, accepted "
    "only in requests and never returned, or a secret such as a password. " + _UNDETERMINED
)
_MULTIVALUE_DESCRIPTION = (
    "Can the attribute hold more than one value? True for arrays or lists and for links or references to a "
    "collection. False for a single value: a scalar, one object or one reference. " + _UNDETERMINED
)
_RETURNED_BY_DEFAULT_DESCRIPTION = (
    "Is the attribute returned when the object is read or searched without extra request options? True when its "
    "values are part of the object's returned representation: a property of the response model, a value shown in "
    "a response example, or a list of links to the referenced objects included in the representation. False when "
    "the representation carries only a single link (href) to a separate collection or sub-resource whose items "
    "must be fetched with another request, when the attribute needs an expand, embed or include option, or when it "
    "is not readable. " + _UNDETERMINED
)


class AttributeBase(BaseModel):
    """
    Base named attribute schema.
    """

    name: str = Field(
        ...,
        description=(
            "The attribute name as it appears in the documentation. For OpenAPI/JSON Schema, use the property name. Preserve original casing and formatting (e.g., 'userName', 'startDate', 'is_active')."
        ),
    )
    description: Optional[str] = Field(
        default=None,
        description="Short description of attribute copied from documentation. Property description from the schema; null if not provided.",
    )


class AttributeTypeFormatBase(AttributeBase):
    """
    Named attribute schema enriched with type and format.
    """

    type: Optional[str] = Field(default=None, description=_TYPE_DESCRIPTION)
    format: Optional[str] = Field(default=None, description=_FORMAT_DESCRIPTION)


class AttributeBooleanFlagsBase(AttributeTypeFormatBase):
    """
    Complete named attribute schema enriched with boolean flags.
    """

    mandatory: Optional[bool] = Field(default=None, description=_MANDATORY_DESCRIPTION)
    updatable: Optional[bool] = Field(default=None, description=_UPDATABLE_DESCRIPTION)
    creatable: Optional[bool] = Field(default=None, description=_CREATABLE_DESCRIPTION)
    readable: Optional[bool] = Field(default=None, description=_READABLE_DESCRIPTION)
    multivalue: Optional[bool] = Field(default=None, description=_MULTIVALUE_DESCRIPTION)
    returnedByDefault: Optional[bool] = Field(default=None, description=_RETURNED_BY_DEFAULT_DESCRIPTION)


class AttributeInfoBase(AttributeBooleanFlagsBase, RelevantDocumentationsMixin):
    """
    Attribute metadata stored under an attribute-name map key.

    This model intentionally does not include `name`; the surrounding map key is
    the stable attribute identifier in persisted/API payloads.
    """

    name: str = Field(
        default="",
        description=(
            "Optional copy of the attribute name for validation compatibility. API and persisted payloads use the "
            "surrounding attributes map key as the canonical name, so this field is not serialized."
        ),
        exclude=True,
    )


class ExtractedAttributeInfoSCIM(AttributeInfoBase):
    """
    LLM extraction model for object class property metadata.
    Contains only fields the LLM should produce.
    """

    scimAttribute: Optional[str] = Field(
        default=None,
        description=(
            "For SCIM mapping scenarios, the source SCIM attribute/path that maps to this application attribute "
            "(e.g., 'userName', 'emails[0].value', 'profile.startDate'). Leave null when not applicable."
        ),
    )


class DiscoveryAttribute(AttributeBase):
    model_config = {"extra": "forbid"}

    relevant_sequences: List[DocSequenceMarker] = Field(
        description=(
            "List of relevant document marker pairs that support the presence of this attribute. "
            "The system attaches the chunk id after validating the markers."
        )
    )


class ValidatedAttributeCandidate(AttributeBase):
    """Discovered attribute supported by sequences verified against a known chunk."""

    model_config = {"extra": "forbid"}

    relevant_sequences: List[DocProcessingSequenceItem] = Field(
        min_length=1,
        description="Verified evidence with its source chunk, matched markers, and extracted text.",
    )


class AttributeDiscoveryResponse(BaseModel):
    """
    Container for extracted attributes of an object class in discovery phase.
    Return an empty list when none are present in the chunk.
    """

    attributes: List[DiscoveryAttribute] = Field(
        default_factory=list,
        description="List of extracted attributes for the object class.",
    )

    model_config = {"extra": "forbid"}


class AttributeInfoRest(AttributeInfoBase):
    relevant_sequences: List[DocSequenceItem] = Field(
        description=("List of relevant document sequences that support the presence of this attribute. ")
    )


class AttributeBuildResponse(AttributeInfoBase):
    """
    Container for extracted attributes of an object class after building the attribute info.
    Return an empty list when none are present in the chunk.
    """


class AttributeTypeFormatBuildResponse(BaseModel):
    """
    LLM response for the type/format enrichment phase.
    """

    type: Optional[str] = Field(default=None, description=_TYPE_DESCRIPTION)
    format: Optional[str] = Field(default=None, description=_FORMAT_DESCRIPTION)

    model_config = {"extra": "forbid"}


class AttributeBooleanFlagsBuildResponse(BaseModel):
    """
    LLM response for the boolean flag enrichment phase.
    """

    mandatory: Optional[bool] = Field(default=None, description=_MANDATORY_DESCRIPTION)
    updatable: Optional[bool] = Field(default=None, description=_UPDATABLE_DESCRIPTION)
    creatable: Optional[bool] = Field(default=None, description=_CREATABLE_DESCRIPTION)
    readable: Optional[bool] = Field(default=None, description=_READABLE_DESCRIPTION)
    multivalue: Optional[bool] = Field(default=None, description=_MULTIVALUE_DESCRIPTION)
    returnedByDefault: Optional[bool] = Field(default=None, description=_RETURNED_BY_DEFAULT_DESCRIPTION)

    model_config = {"extra": "forbid"}


class AttributeInfoScim(AttributeInfoBase):
    """
    Attribute metadata for an object class property as described in OpenAPI/JSON Schema.
    """

    scimAttribute: Optional[str] = Field(
        default=None,
        description=(
            "For SCIM mapping scenarios, the source SCIM attribute/path that maps to this application attribute "
            "(e.g., 'userName', 'emails[0].value', 'profile.startDate'). Leave null when not applicable."
        ),
    )


class SqlForeignKey(BaseModel):
    """Physical target of a SQL foreign-key column."""

    constraintName: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Database constraint name when the source provides it.",
    )
    referencedTable: str = Field(..., min_length=1, description="Referenced physical database table.")
    referencedColumn: str = Field(..., min_length=1, description="Referenced physical database column.")

    model_config = {"extra": "forbid"}


class AttributeInfoSql(AttributeInfoBase):
    """Attribute metadata with its physical database binding."""

    databaseCatalog: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Database catalog containing the physical table when supplied by the source.",
    )
    databaseSchema: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Database schema containing the physical table when supplied by the source.",
    )
    table: str = Field(
        ...,
        min_length=1,
        description="Physical database table containing the attribute.",
    )
    column: str = Field(
        ...,
        min_length=1,
        description="Physical database column mapped to the attribute.",
    )
    primaryKey: Optional[bool] = Field(
        default=None,
        description="Whether the column belongs to the table primary key.",
    )
    foreignKey: Optional[SqlForeignKey] = Field(
        default=None,
        description="Physical foreign-key target declared for the column.",
    )
    databaseType: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Native SQL/JDBC type name supplied by the database schema source.",
    )
    nullable: Optional[bool] = Field(
        default=None,
        description="Whether the physical database column accepts NULL.",
    )
    unique: Optional[bool] = Field(
        default=None,
        description="Whether the physical database column has a uniqueness constraint.",
    )
    generated: Optional[bool] = Field(
        default=None,
        description="Whether the physical database column is generated by the database.",
    )
    defaultValue: Any = Field(
        default=None,
        description="Physical database default supplied by the schema source.",
    )


class SqlPhysicalTable(BaseModel):
    """Identity of the physical table that supplied SQL attributes."""

    databaseCatalog: Optional[str] = Field(default=None, min_length=1)
    databaseSchema: Optional[str] = Field(default=None, min_length=1)
    table: str = Field(..., min_length=1)
    tableType: Optional[str] = Field(default=None, min_length=1)

    model_config = {"extra": "forbid"}


class SqlConnectorAttribute(BaseModel):
    """One desired ConnId projection attribute from a Conndev export."""

    name: str = Field(..., min_length=1)
    connIdType: Optional[str] = Field(default=None, min_length=1)
    column: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Explicit SQL path from Conndev; never inferred from the attribute name.",
    )
    mandatory: Optional[bool] = None
    creatable: Optional[bool] = None
    updatable: Optional[bool] = None

    model_config = {"extra": "forbid"}


class SqlConnectorObjectClass(BaseModel):
    """Desired ConnId-facing object-class projection from Conndev."""

    name: str = Field(..., min_length=1)
    attributes: List[SqlConnectorAttribute] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class SqlContext(BaseModel):
    """Class-specific physical SQL identity and separate ConnId projection."""

    physicalTable: SqlPhysicalTable
    connectorObjectClass: Optional[SqlConnectorObjectClass] = None

    model_config = {"extra": "forbid"}


class AttributeProcessingInfo(AttributeBooleanFlagsBase, RelevantDocumentationsMixin):
    relevant_sequences: List[DocProcessingSequenceItem] = Field(
        description=("List of document sequences that support the presence of this attribute, includes full text")
    )


class AttributeDedupResponse(BaseModel):
    """
    Container for deduplication LLM output for attributes.
    """

    duplicates: List[Tuple[str, str]] = Field(
        ...,
        description=(
            "List of pairs of duplicate attributes. The pair consists of two attribute names that are considered duplicates. One with more complete documentation should be first"
        ),
    )

    to_be_deleted: List[str] = Field(
        ...,
        description=("List of attribute names to be deleted because of having weak documentation or being irrelevant"),
    )


class AttributeResponse(BaseModel):
    """
    Attribute map for a specific object class where each key is the property name.
    Return an empty map when the object class has no properties in the fragment.
    """

    attributes: Dict[str, AttributeInfoSql | AttributeInfoScim | AttributeInfoRest] = Field(
        default_factory=dict,
        description="Map of attribute name to its normalized metadata (AttributeInfo).",
    )
    scimContext: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Class-specific SCIM schema, resource and ConnId projection used by SCIM code generation. "
            "Empty for non-SCIM object classes."
        ),
    )
    sqlContext: Optional[SqlContext] = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description=(
            "Physical SQL table identity and optional Conndev ConnId projection. Absent for non-SQL object classes."
        ),
    )


class ExtractedAttributeResponseSCIM(BaseModel):
    """
    LLM extraction response for attributes.
    """

    attributes: Dict[str, ExtractedAttributeInfoSCIM] = Field(
        default_factory=dict,
        description="Map of attribute name to extracted metadata.",
    )


# --- Attributes ---
