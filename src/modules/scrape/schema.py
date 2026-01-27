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
