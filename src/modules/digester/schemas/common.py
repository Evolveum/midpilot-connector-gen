# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Annotated, Any, Dict, List

from pydantic import BaseModel, BeforeValidator, Field, field_serializer, field_validator

from src.common.schema import CamelCaseModel
from src.common.utils.normalize import normalize_relevant_documentation_refs
from src.modules.digester.enums import EndpointMethod


def normalize_http_method(value: Any) -> Any:
    """Accept lowercase/mixed-case HTTP methods and normalize them before enum validation."""
    if isinstance(value, str):
        return value.strip().upper()
    return value


NormalizedEndpointMethod = Annotated[EndpointMethod, BeforeValidator(normalize_http_method)]
"""``EndpointMethod`` that tolerates lowercase/whitespace-padded LLM output."""


class ChunkReference(CamelCaseModel):
    """
    Internal reference to one documentation chunk.

    The model accepts both API camelCase and internal snake_case keys, but
    normalizes all runtime use to snake_case.
    """

    doc_id: str = Field(
        ...,
        description="Unique identifier for the source documentation item.",
    )
    chunk_id: str = Field(
        ...,
        description="Unique identifier for the documentation chunk.",
    )

    def to_internal_dict(self) -> dict[str, str]:
        return {"doc_id": self.doc_id, "chunk_id": self.chunk_id}

    def to_api_dict(self) -> dict[str, str]:
        return self.model_dump(by_alias=True)


class DocSequenceItem(CamelCaseModel):
    """
    Represents a sequence from a chunk relevant to the extracted information.
    """

    chunk_id: str = Field(
        ...,
        description="Unique identifier for the document chunk.",
    )
    start_sequence: str = Field(
        ...,
        description="Unique token / word sequence that identifies the start of the relevant chunk.",
    )
    end_sequence: str = Field(
        ...,
        description="Unique token / word sequence that identifies the end of the relevant chunk.",
    )


class DocSequenceMarker(CamelCaseModel):
    """
    Marker pair returned by the LLM before the system attaches the known chunk id.
    """

    model_config = {"extra": "forbid"}

    start_sequence: str = Field(
        ...,
        description="Unique token / word sequence that identifies the start of the relevant chunk.",
    )
    end_sequence: str = Field(
        ...,
        description="Unique token / word sequence that identifies the end of the relevant chunk.",
    )


class DocProcessingSequenceItem(DocSequenceItem):
    """
    DocSequenceItem with full text field for easier processing.
    """

    text: str = Field(
        ..., description="Full text of the document chunk from start_sequence to end_sequence for processing."
    )


class RelevantDocumentationsMixin(BaseModel):
    """
    Shared ``relevant_documentations`` field for persisted/API metadata models.

    The field is system-populated (never filled by the LLM). It accepts loose
    snake_case/camelCase chunk references on input and always serializes to a list
    of ``{"docId", "chunkId"}`` UUID strings.
    """

    relevant_documentations: List[Dict[str, str]] = Field(
        default_factory=list,
        validation_alias="relevantDocumentations",
        serialization_alias="relevantDocumentations",
        description=(
            "List of chunks that contain evidence for this entity. "
            "Each entry is serialized as 'docId' and 'chunkId' UUID strings. "
            "This field is populated automatically by the system and should NOT be filled by the LLM."
        ),
    )

    model_config = {"validate_by_name": True}

    @field_validator("relevant_documentations", mode="before")
    @classmethod
    def _validate_relevant_documentations(cls, v: Any) -> List[Dict[str, str]]:
        return normalize_relevant_documentation_refs(v)

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


class DocMarkerMatch(BaseModel):
    """
    Represents the actual matched marker in the document text after fuzzy matching.
    """

    start_position: int = Field(
        ..., description="Character index of the start of the matched sequence in the original document text."
    )
    start_position_collapsed: int = Field(
        ...,
        description="Character index of the start of the matched sequence in the collapsed text used for fuzzy matching.",
    )
    end_position: int = Field(
        ..., description="Character index of the end of the matched sequence in the original document text."
    )
    end_position_collapsed: int = Field(
        ...,
        description="Character index of the end of the matched sequence in the collapsed text used for fuzzy matching.",
    )
    distance: int = Field(
        ...,
        description="Levenshtein distance between the matched sequence and the original marker, used for confidence scoring.",
    )
