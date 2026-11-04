# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator

from src.config import config
from src.documents import normalize_count


class ChunkProcessingError(BaseModel):
    """
    A non-fatal failure while processing a single documentation chunk.

    Collected so a transient per-chunk failure (e.g. an LLM connection error) can be
    surfaced as a job error without aborting the whole scrape run.
    """

    url: str = Field(description="URL of the documentation the failed chunk belongs to")
    chunk_index: int = Field(description="Index of the failed chunk within the documentation")
    error: str = Field(description="Human-readable description of the failure")


class ObjectClassMention(BaseModel):
    """An object class the LLM found in a chunk, with the quote that shows it."""

    name: str = Field(description="Singular name of the object class as the API uses it, for example WorkPackage")
    evidence: str = Field(default="", description="Short verbatim quote from the chunk that contains the class name")

    @field_validator("evidence", mode="before")
    @classmethod
    def normalize_evidence(cls, value: Any) -> Any:
        return "" if value is None else value


class LlmChunkOutput(BaseModel):
    """
    Schema for LLM output containing summary, tags and category for a chunk
    """

    summary: str = Field(description="The generated summary of the chunk content")
    num_endpoints: int = Field(description="The number of endpoints defined in the chunk content")
    tags: List[str] = Field(
        description='List of tags that could describe the content in the chunk, for example: ["endpoints", "authorization"]'
    )
    category: str = Field(description="Type of the content in the chunk")
    num_defined_object_classes: Optional[int] = Field(
        default=None, description="The number of defined object classes mentioned in the chunk, if any"
    )
    object_classes: List[ObjectClassMention] = Field(
        default_factory=list,
        description="Object classes whose schema, attributes or endpoints the chunk defines, with evidence",
    )

    @field_validator("object_classes", mode="before")
    @classmethod
    def normalize_object_classes(cls, value: Any) -> Any:
        """Accept a bare class name (it has no evidence, so it is dropped later) and skip unnamed entries."""
        if not isinstance(value, list):
            return []
        return [
            {"name": item} if isinstance(item, str) else item
            for item in value
            if isinstance(item, str) or (isinstance(item, dict) and isinstance(item.get("name"), str))
        ]

    @field_validator("num_endpoints", mode="before")
    @classmethod
    def normalize_num_endpoints(cls, value: Any) -> Any:
        return normalize_count(value)

    @field_validator("category")
    @classmethod
    def validate_categories(cls, v: str) -> str:
        """Validate that categories are from the configured list"""

        if v not in config.scrape_and_process.chunk_categories:
            raise ValueError(f"Invalid category: {v}. Must be one of: {config.scrape_and_process.chunk_categories}")
        return v
