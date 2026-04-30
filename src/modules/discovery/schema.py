# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.


from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

DiscoveryIntegrationType = Literal["SCIM", "REST", "DUMMY"]


class IrrelevantLinks(BaseModel):
    """
    Schema for LLM output containing irrelevant links
    """

    links: List[str] = Field(description="List of links deemed irrelevant")


class RankedLinks(BaseModel):
    """
    Schema for LLM output containing ranked links
    """

    links: List[str] = Field(description="List of links ordered from most relevant to least relevant")


class CandidateLinksInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    @field_validator("integration_type", mode="before")
    @classmethod
    def normalize_integration_type(cls, value: Any) -> str:
        if value is None:
            return "DUMMY"
        if not isinstance(value, str):
            raise TypeError("integrationType must be a string")
        normalized = value.strip().upper()
        if normalized not in {"SCIM", "REST", "DUMMY"}:
            raise ValueError("integrationType must be one of: SCIM, REST, DUMMY")
        return normalized

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
    integration_type: DiscoveryIntegrationType = Field(
        default="DUMMY",
        serialization_alias="integrationType",
        validation_alias="integrationType",
        description="Discovery protocol priority: SCIM, REST, or DUMMY.",
    )
    llm_generated_search_query: bool = Field(
        default=False,
        serialization_alias="llmGeneratedSearchQuery",
        validation_alias="llmGeneratedSearchQuery",
        description="Use LLM to generate web search queries (default to use templates)",
    )
    skip_cache: bool = Field(
        default=False,
        serialization_alias="skipCache",
        validation_alias="skipCache",
        description="Whether to skip cached discovery output when input is the same.",
    )
    enable_link_filtering: bool = Field(
        default=True,
        serialization_alias="enableLinkFiltering",
        validation_alias="enableLinkFiltering",
        description="Enable LLM-based filtering of irrelevant links",
    )
    enable_link_ranking: bool = Field(
        default=True,
        serialization_alias="enableLinkRanking",
        validation_alias="enableLinkRanking",
        description="Enable LLM-based ranking of candidate links",
    )
    num_queries: int = Field(
        default=8,
        ge=1,
        le=8,
        serialization_alias="numQueries",
        validation_alias="numQueries",
        description="How many distinct search queries to run during discovery (allowed range: 1-8).",
    )
    max_results_per_query: int = Field(
        default=10,
        serialization_alias="maxResultsPerQuery",
        validation_alias="maxResultsPerQuery",
        description="How many results to fetch per query (per backend)",
    )
    max_candidate_links: int = Field(
        default=10,
        serialization_alias="maxCandidateLinks",
        validation_alias="maxCandidateLinks",
        description="Max number of candidate links to return after ranking/selection",
    )
    max_filter_llm_calls: int = Field(
        default=3,
        serialization_alias="maxFilterLlmCalls",
        validation_alias="maxFilterLlmCalls",
        description="Maximum number of LLM calls for filtering irrelevant links",
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
