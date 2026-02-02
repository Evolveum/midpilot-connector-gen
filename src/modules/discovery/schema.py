# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.


from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class IrrelevantLinks(BaseModel):
    """
    Schema for LLM output containing irrelevant links
    """

    links: List[str] = Field(description="List of links deemed irrelevant")


class CandidateLinksInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    application_name: str = Field(
        ...,
        serialization_alias="applicationName",
        validation_alias="applicationName",
        description="Target application name",
    )
    application_version: str = Field(
        default="latest",
        serialization_alias="applicationVersion",
        validation_alias="applicationVersion",
        description="Optional version string",
    )
    llm_generated_search_query: bool = Field(
        default=False,
        serialization_alias="llmGeneratedSearchQuery",
        validation_alias="llmGeneratedSearchQuery",
        description="Use LLM to generate web search queries (default to use templates)",
    )

    # New knobs (safe defaults, fully optional)
    num_queries: int = Field(
        default=5,
        serialization_alias="numQueries",
        validation_alias="numQueries",
        description="How many distinct search queries to run during discovery",
    )
    max_results_per_query: int = Field(
        default=10,
        serialization_alias="maxResultsPerQuery",
        validation_alias="maxResultsPerQuery",
        description="How many results to fetch per query (per backend)",
    )


class CandidateLinksOutput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    candidate_links: List[str] = Field(
        default_factory=list,
        serialization_alias="candidateLinks",
        validation_alias="candidateLinks",
        description="Selected links to crawl",
    )
    candidate_links_enriched: List[Dict[str, Any]] = Field(
        default_factory=list,
        serialization_alias="candidateLinksEnriched",
        validation_alias="candidateLinksEnriched",
        description="Selected links to crawl with additional information",
    )


class SearchResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(..., description="Result title")
    href: str = Field(..., description="Result URL")
    body: str = Field(..., description="Result summary/snippet")
    source: str = Field(..., description="Search backend source")


# --- Pydantic models used only for LLM output parsing ---


class PySearchPrompts(BaseModel):
    """Search prompts produced by the LLM.

    Supports both:
    - a single string (legacy): `searchPrompt`
    - a list of strings (new): `searchPrompts`
    """

    model_config = ConfigDict(populate_by_name=True)

    search_prompts: List[str] = Field(
        default_factory=list,
        serialization_alias="searchPrompts",
        validation_alias="searchPrompts",
        description="List of search queries to run.",
        min_length=1,
    )


class PyScrapeFetchReferences(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="Name of the scraping batch or target.")
    urls_to_crawl: List[str] = Field(
        default_factory=list,
        serialization_alias="urlsToCrawl",
        validation_alias="urlsToCrawl",
        description="A list of links containing all URLs mentioned in the message.",
    )
    text_output: Optional[str] = Field(
        default=None,
        serialization_alias="textOutput",
        validation_alias="textOutput",
        description="Optional notes or free-form text returned by the evaluator.",
    )


@dataclass(frozen=True)
class DiscoverySearchBatch:
    query: str
    results: List[SearchResult]
