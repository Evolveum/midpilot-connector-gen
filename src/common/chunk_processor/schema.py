# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import uuid
from typing import List, Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator

from src.config import config
from src.modules.scrape.schema import DocumentationReferences


class SummaryOutput(BaseModel):
    """
    Schema for LLM output containing summary, number of endpoints, if it is overview documentation, if it is index documentation, and if it contains detailed authentication methods
    """

    summary: str = Field(description="The generated summary of the content")
    num_endpoints: int = Field(description="The number of endpoints defined in the content")
    has_authentication: bool = Field(description="Indicates if the content contains detailed authentication methods")
    is_overview: bool = Field(description="Indicates if the content is an overview/introduction documentation")
    is_index: bool = Field(description="Indicates if the content is a navigational/index documentation")

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "num_endpoints": self.num_endpoints,
            "has_authentication": self.has_authentication,
            "is_overview": self.is_overview,
            "is_index": self.is_index,
        }


class SavedDocumentation(BaseModel):
    """
    Schema for a saved documentation after scraping or processing uploaded file
    """

    url: str
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    contentType: Optional[str] = None
    content: Optional[str] = None
    documentationReferences: Optional[DocumentationReferences] = None
    summary: Optional[SummaryOutput] = None
    links: Optional[List[HttpUrl]] = None

    def to_dict(self) -> dict:
        return {
            "url": str(self.url),
            "contentType": self.contentType,
            "content": self.content,
            "documentationReferences": self.documentationReferences.to_dict() if self.documentationReferences else None,
            "summary": self.summary.to_dict() if self.summary else None,
            "links": [str(link) for link in self.links] if self.links else None,
        }


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

    @field_validator("category")
    @classmethod
    def validate_categories(cls, v: str) -> str:
        """Validate that categories are from the configured list"""

        if v not in config.scrape_and_process.chunk_categories:
            raise ValueError(f"Invalid category: {v}. Must be one of: {config.scrape_and_process.chunk_categories}")
        return v
