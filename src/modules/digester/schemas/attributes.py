# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, field_serializer, field_validator

from src.modules.digester.schemas.common import DocProcessingSequenceItem, DocSequenceItem, DocSequenceMarker

# --- Attributes ---


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


class AttributeBooleanFlagsBase(AttributeTypeFormatBase):
    """
    Complete named attribute schema enriched with boolean flags.
    """

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


class AttributeRelevantDocumentationsMixin(BaseModel):
    """
    Shared relevant-documentation field for persisted/API attribute metadata.
    """

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

    model_config = {"validate_by_name": True}

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


class AttributeInfoBase(AttributeBooleanFlagsBase, AttributeRelevantDocumentationsMixin):
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

    type: Optional[str] = Field(
        default=None,
        description="Attribute type found or corrected during type/format enrichment. Use null if unknown.",
    )
    format: Optional[str] = Field(
        default=None,
        description="Attribute format found or corrected during type/format enrichment. Use null if unknown.",
    )

    model_config = {"extra": "forbid"}


class AttributeBooleanFlagsBuildResponse(BaseModel):
    """
    LLM response for the boolean flag enrichment phase.
    """

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


class AttributeProcessingInfo(AttributeBooleanFlagsBase, AttributeRelevantDocumentationsMixin):
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

    attributes: Dict[str, AttributeInfoScim | AttributeInfoRest] = Field(
        default_factory=dict,
        description="Map of attribute name to its normalized metadata (AttributeInfo).",
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
