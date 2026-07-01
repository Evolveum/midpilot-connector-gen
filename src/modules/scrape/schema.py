# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import List

from pydantic import BaseModel, ConfigDict, Field

from src.common.session.schema import Documentation

__all__ = [
    "RelevantLinks",
    "ScrapeRequest",
    "ScrapeResult",
]


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
    skip_cache: bool = Field(
        False,
        description="Whether to skip already processed data from a different session when scraper input is the same.",
        validation_alias="skipCache",
        serialization_alias="skipCache",
    )


class ScrapeResult(BaseModel):
    finish_reason: str = Field(serialization_alias="finishReason")
    saved_documentations_count: int = Field(serialization_alias="savedDocumentationsCount")
    saved_chunks_count: int = Field(serialization_alias="savedChunksCount")
    saved_documentations: List[Documentation] = Field(serialization_alias="savedDocumentations")


class RelevantLinks(BaseModel):
    """
    Schema for LLM output containing relevant links
    """

    links: List[str] = Field(description="List of links deemed relevant")
