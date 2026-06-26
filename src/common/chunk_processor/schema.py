# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator

from src.common.documentation import normalize_count
from src.config import config


class ChunkProcessingError(BaseModel):
    """
    A non-fatal failure while processing a single documentation chunk.

    Collected so a transient per-chunk failure (e.g. an LLM connection error) can be
    surfaced as a job error without aborting the whole scrape run.
    """

    url: str = Field(description="URL of the documentation the failed chunk belongs to")
    chunk_index: int = Field(description="Index of the failed chunk within the documentation")
    error: str = Field(description="Human-readable description of the failure")


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
    different_app_name: bool = Field(
        description="Indicates if the chunk mentions a different application name than expected"
    )
    num_defined_object_classes: Optional[int] = Field(
        default=None, description="The number of defined object classes mentioned in the chunk, if any"
    )

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
