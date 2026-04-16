# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import List

from pydantic import BaseModel, ConfigDict, Field

from src.common.session.schema import Documentation


class ScrapeRequest(BaseModel):
    """
    Input payload to start the scrape job.
    """

    model_config = ConfigDict(populate_by_name=True)

    starter_links: List[str] = Field(
        ..., description="Initial URLs to scrape", validation_alias="starterLinks", serialization_alias="starterLinks"
    )
    application_name: str = Field(
        ..., description="Application name", validation_alias="applicationName", serialization_alias="applicationName"
    )
    application_version: str = Field(
        default="current",
        description="Application version. If omitted, discoveryInput.applicationVersion is used when available, otherwise 'current'.",
        validation_alias="applicationVersion",
        serialization_alias="applicationVersion",
    )
    use_previous_session_data: bool = Field(
        True,
        description="Whether to use the already processed data from a different session if the scraper input is the same.",
        validation_alias="usePreviousSessionData",
        serialization_alias="usePreviousSessionData",
    )


class ScrapeResult(BaseModel):
    finish_reason: str = Field(serialization_alias="finishReason")
    saved_documentations_count: int = Field(serialization_alias="savedDocumentationsCount")
    saved_chunks_count: int = Field(serialization_alias="savedChunksCount")
    saved_documentations: List[Documentation] = Field(serialization_alias="savedDocumentations")


class IrrelevantLinks(BaseModel):
    """
    Schema for LLM output containing irrelevant links
    """

    links: List[str] = Field(description="List of links deemed irrelevant")


class RelevantLinks(BaseModel):
    """
    Schema for LLM output containing relevant links
    """

    links: List[str] = Field(description="List of links deemed relevant")


class ReferenceItem(BaseModel):
    """
    Individual reference item extracted from a documentation
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
    References extracted from a documentation using crawl4ai markdown generator
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
