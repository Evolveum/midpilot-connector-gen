# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Dict, List

from pydantic import BaseModel, Field


class ScrapeRequest(BaseModel):
    """
    Input payload to start the scrape job.
    """

    starter_links: List[str] = Field(
        ..., description="Initial URLs to scrape", validation_alias="starterLinks", serialization_alias="starterLinks"
    )
    application_name: str = Field(
        ..., description="Application name", validation_alias="applicationName", serialization_alias="applicationName"
    )
    application_version: str = Field(
        ...,
        description="Application version (e.g., 'current')",
        validation_alias="applicationVersion",
        serialization_alias="applicationVersion",
    )

    # forbidden_url_parts: List[str] = Field(
    #     default_factory=lambda: [
    #         "logout",
    #         "login",
    #         "signup",
    #         "register",
    #         "subscribe",
    #         "pricing",
    #         "plans",
    #         "terms",
    #         "privacy",
    #         "contact",
    #         "about",
    #         "blog",
    #         "news",
    #         "forum",
    #         "release-notes",
    #         "changelog",
    #         "es",
    #         "pt",
    #         "de",
    #         "fr",
    #         "jp",
    #         "zh",
    #         "sk",
    #         "ru",
    #         "it",
    #         "nl",
    #         "pl",
    #         "tr",
    #     ],
    #     description="URL substrings to consider irrelevant a priori",
    #     validation_alias="forbiddenUrlParts",
    #     serialization_alias="forbiddenUrlParts",
    # )


class ScrapeResult(BaseModel):
    finish_reason: str = Field(serialization_alias="finishReason")
    saved_pages_count: int = Field(serialization_alias="savedPagesCount")
    page_chunks_count: int = Field(serialization_alias="pageChunksCount")
    saved_pages: Dict[str, dict] = Field(serialization_alias="savedPages")


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
    Individual reference item extracted from a page
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


class PageReferences(BaseModel):
    """
    References extracted from a page using crawl4ai markdown generator
    """

    page_url: str = Field(description="The URL of the page from which references were extracted")
    references: List[ReferenceItem] = Field(
        description="List of structured reference items with URL, description, and number"
    )
    references_markdown: str = Field(description="Markdown of references in the format from the crawl4ai generator")
    text_with_citations: str = Field(description="Markdown string containing in-text citations")

    def to_dict(self) -> dict:
        return {
            "page_url": self.page_url,
            "references": [ref.to_dict() for ref in self.references],
            "references_markdown": self.references_markdown,
            "text_with_citations": self.text_with_citations,
        }
