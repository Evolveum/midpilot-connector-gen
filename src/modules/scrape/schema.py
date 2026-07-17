# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import List

from pydantic import BaseModel, Field

from src.common.schema import CamelCaseModel
from src.common.session.schema import Documentation

__all__ = [
    "RelevantLinks",
    "ScrapeRequest",
    "ScrapeResult",
]


class ScrapeRequest(CamelCaseModel):
    """
    Input payload to start the scrape job.
    """

    starter_links: List[str] = Field(..., description="Initial URLs to scrape")
    application_name: str = Field(..., description="Application name")
    application_version: str = Field(
        default="current",
        description="Application version. If omitted, discoveryInput.applicationVersion is used when available, otherwise 'current'.",
    )
    skip_cache: bool = Field(
        False,
        description="Whether to skip already processed data from a different session when scraper input is the same.",
    )


class ScrapeResult(CamelCaseModel):
    finish_reason: str
    saved_documentations_count: int
    saved_chunks_count: int
    saved_documentations: List[Documentation]


class RelevantLinks(BaseModel):
    """
    Schema for LLM output containing relevant links
    """

    links: List[str] = Field(description="List of links deemed relevant")
