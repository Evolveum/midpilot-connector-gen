# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.


from dataclasses import dataclass
from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field, field_validator

from src.core.schema import CamelCaseModel
from src.integrations.web import SearchResult

DiscoveryIntegrationType = Literal["scim", "rest", "dummy"]


class RankedLinks(BaseModel):
    """
    Schema for LLM output containing ranked links
    """

    links: List[str] = Field(description="List of links ordered from most relevant to least relevant")


class CandidateLinksInput(CamelCaseModel):
    @field_validator("integration_type", mode="before")
    @classmethod
    def normalize_integration_type(cls, value: Any) -> str:
        if value is None:
            return "dummy"
        if not isinstance(value, str):
            raise TypeError("integrationType must be a string")
        normalized = value.strip().lower()
        if normalized not in {"scim", "rest", "dummy"}:
            raise ValueError("integrationType must be one of: scim, rest, dummy")
        return normalized

    application_name: str = Field(
        ...,
        description="Target application name",
    )
    application_version: str = Field(
        default="latest",
        description="Optional version string",
    )
    integration_type: DiscoveryIntegrationType = Field(
        default="dummy",
        description="Discovery protocol priority: scim, rest, or dummy.",
    )
    llm_generated_search_query: bool = Field(
        default=False,
        description="Use LLM to generate web search queries (default to use templates)",
    )
    skip_cache: bool = Field(
        default=False,
        description="Whether to skip cached discovery output when input is the same.",
    )
    enable_link_filtering: bool = Field(
        default=True,
        description="Enable LLM-based filtering of irrelevant links",
    )
    enable_link_ranking: bool = Field(
        default=True,
        description="Enable LLM-based ranking of candidate links",
    )
    num_queries: int = Field(
        default=8,
        ge=1,
        le=8,
        description="How many distinct search queries to run during discovery (allowed range: 1-8).",
    )
    max_results_per_query: int = Field(
        default=10,
        description="How many results to fetch per query (per backend)",
    )
    max_candidate_links: int = Field(
        default=10,
        description="Max number of candidate links to return after ranking/selection",
    )
    max_filter_llm_calls: int = Field(
        default=3,
        description="Maximum number of LLM calls for filtering irrelevant links",
    )


class CandidateLinksOutput(CamelCaseModel):
    candidate_links: List[str] = Field(
        default_factory=list,
        description="Selected links to crawl",
    )
    candidate_links_enriched: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Selected links to crawl with additional information",
    )


# --- Pydantic models used only for LLM output parsing ---


class PySearchPrompts(CamelCaseModel):
    """Search prompts produced by the LLM.

    Supports both:
    - a single string (legacy): `searchPrompt`
    - a list of strings (new): `searchPrompts`
    """

    search_prompts: List[str] = Field(
        default_factory=list,
        description="List of search queries to run.",
        min_length=1,
    )


@dataclass(frozen=True)
class DiscoverySearchBatch:
    query: str
    results: List[SearchResult]
