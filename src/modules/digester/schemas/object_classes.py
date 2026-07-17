# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any, Dict, List, Optional

from pydantic import Field

from src.common.schema import CamelCaseModel
from src.modules.digester.enums import ConfidenceLevel, RelevantLevel
from src.modules.digester.schemas.common import RelevantDocumentationsMixin

# --- Object Classes ---


class BaseObjectClass(CamelCaseModel):
    """
    Minimal shared representation.
    Used when only identity + short meaning of the class are needed.
    """

    model_config = {
        "json_schema_extra": {
            "exclude_none": True,
        },
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


class FinalObjectClass(RankedObjectClass, RelevantDocumentationsMixin):
    """
    Final user-facing object class model.
    Adds system-populated fields not used in LLM ranking prompts.
    """

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


class ObjectClassesResponse(CamelCaseModel):
    """
    Final object classes returned to API consumers.
    """

    object_classes: List[FinalObjectClass] = Field(
        default_factory=list,
        description=(
            "List of extracted object classes enriched with confidence and returned in final order. "
            "Use alias 'objectClasses' in JSON payloads."
        ),
    )

    @property
    def objectClasses(self) -> List[FinalObjectClass]:
        return self.object_classes


class ObjectClassesExtendedResponse(CamelCaseModel):
    """
    First LLM call response container.
    """

    object_classes: List[ExtendedObjectClass] = Field(
        default_factory=list,
        description=(
            "List of extracted extended object classes from the first pass. Use alias 'objectClasses' in JSON payloads."
        ),
    )

    @property
    def objectClasses(self) -> List[ExtendedObjectClass]:
        return self.object_classes


class ObjectClassesConfidenceResponse(CamelCaseModel):
    """
    Second LLM call response container.
    """

    object_classes: List[ObjectClassWithConfidence] = Field(
        default_factory=list,
        description="List of object classes with assigned confidence levels.",
    )

    @property
    def objectClasses(self) -> List[ObjectClassWithConfidence]:
        return self.object_classes


class ObjectClassesRankedResponse(CamelCaseModel):
    """
    Third LLM call response container.
    """

    object_classes: List[RankedObjectClass] = Field(
        default_factory=list,
        description=(
            "Reordered list of ranked object classes. "
            "Each item includes fields needed for ranking and final output composition."
        ),
    )

    @property
    def objectClasses(self) -> List[RankedObjectClass]:
        return self.object_classes
