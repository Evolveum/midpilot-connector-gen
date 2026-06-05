# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from pydantic import AliasChoices, BaseModel, Field


class DocSequenceItem(BaseModel):
    """
    Represents a sequence from a chunk relevant to the extracted information.
    """

    model_config = {"populate_by_name": True}

    chunk_id: str = Field(
        ...,
        validation_alias=AliasChoices("chunk_id", "chunkId"),
        serialization_alias="chunkId",
        description="Unique identifier for the document chunk.",
    )
    start_sequence: str = Field(
        ...,
        description="Unique token / word sequence that identifies the start of the relevant chunk.",
        validation_alias=AliasChoices("start_sequence", "startSequence"),
        serialization_alias="startSequence",
    )
    end_sequence: str = Field(
        ...,
        description="Unique token / word sequence that identifies the end of the relevant chunk.",
        validation_alias=AliasChoices("end_sequence", "endSequence"),
        serialization_alias="endSequence",
    )


class DocSequenceMarker(BaseModel):
    """
    Marker pair returned by the LLM before the system attaches the known chunk id.
    """

    model_config = {"extra": "forbid", "populate_by_name": True}

    start_sequence: str = Field(
        ...,
        description="Unique token / word sequence that identifies the start of the relevant chunk.",
        validation_alias=AliasChoices("start_sequence", "startSequence"),
        serialization_alias="startSequence",
    )
    end_sequence: str = Field(
        ...,
        description="Unique token / word sequence that identifies the end of the relevant chunk.",
        validation_alias=AliasChoices("end_sequence", "endSequence"),
        serialization_alias="endSequence",
    )


class DocProcessingSequenceItem(DocSequenceItem):
    """
    DocSequenceItem with full text field for easier processing.
    """

    text: str = Field(
        ..., description="Full text of the document chunk from start_sequence to end_sequence for processing."
    )


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
