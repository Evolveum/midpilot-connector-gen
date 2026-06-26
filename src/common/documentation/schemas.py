# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import uuid
from typing import Any, List, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, HttpUrl, field_validator


def normalize_count(value: Any) -> Any:
    """Normalize nullish LLM count outputs to zero before Pydantic integer parsing."""
    if value is None:
        return 0
    if isinstance(value, str) and value.strip().lower() in {"", "null", "none", "unknown", "n/a"}:
        return 0
    return value


class SummaryOutput(BaseModel):
    """
    Schema for LLM output containing summary, endpoint count, and high-level documentation flags.
    """

    summary: str = Field(description="The generated summary of the content")
    num_endpoints: Optional[int] = Field(default=None, description="The number of endpoints defined in the content")
    has_authentication: bool = Field(description="Indicates if the content contains detailed authentication methods")
    is_overview: bool = Field(description="Indicates if the content is an overview/introduction documentation")
    is_index: bool = Field(description="Indicates if the content is a navigational/index documentation")

    @field_validator("num_endpoints", mode="before")
    @classmethod
    def normalize_num_endpoints(cls, value: Any) -> Any:
        return normalize_count(value)

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "num_endpoints": self.num_endpoints,
            "has_authentication": self.has_authentication,
            "is_overview": self.is_overview,
            "is_index": self.is_index,
        }


class ReferenceItem(BaseModel):
    """
    Individual reference item extracted from documentation.
    """

    url: str = Field(description="The URL of the reference")
    description: str = Field(description="Description or context for the reference")
    number: int = Field(description="Unique number assigned to the reference for citation")

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "description": self.description,
            "number": self.number,
        }


class DocumentationReferences(BaseModel):
    """
    References extracted from documentation using crawl4ai markdown generation.
    """

    documentation_url: str = Field(description="The URL of the documentation from which references were extracted")
    references: List[ReferenceItem] = Field(
        description="List of structured reference items with URL, description, and number"
    )
    references_markdown: str = Field(description="Markdown of references in the format from the crawl4ai generator")
    text_with_citations: str = Field(description="Markdown string containing in-text citations")

    def to_dict(self) -> dict:
        return {
            "documentation_url": self.documentation_url,
            "references": [ref.to_dict() for ref in self.references],
            "references_markdown": self.references_markdown,
            "text_with_citations": self.text_with_citations,
        }


class SavedDocumentation(BaseModel):
    """
    Scraped or uploaded documentation prepared for chunk processing.
    """

    model_config = ConfigDict(populate_by_name=True)

    url: str
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    content_type: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("content_type", "contentType"),
        serialization_alias="contentType",
    )
    content: Optional[str] = None
    documentation_references: Optional[DocumentationReferences] = Field(
        default=None,
        validation_alias=AliasChoices("documentation_references", "documentationReferences"),
        serialization_alias="documentationReferences",
    )
    summary: Optional[SummaryOutput] = None
    links: Optional[List[HttpUrl]] = None

    def to_dict(self) -> dict:
        return {
            "url": str(self.url),
            "contentType": self.content_type,
            "content": self.content,
            "documentationReferences": (
                self.documentation_references.to_dict() if self.documentation_references else None
            ),
            "summary": self.summary.to_dict() if self.summary else None,
            "links": [str(link) for link in self.links] if self.links else None,
        }
