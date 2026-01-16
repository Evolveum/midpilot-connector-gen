#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


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
        description="Use llm to generate web search query (defaults to using string template)",
    )


class CandidateLinksOutput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    candidate_links: List[str] = Field(
        default_factory=list,
        serialization_alias="candidateLinks",
        validation_alias="candidateLinks",
        description="Selected links to crawl",
    )
    candidate_links_enriched: List[dict] = Field(
        default_factory=list,
        serialization_alias="candidateLinksEnriched",
        validation_alias="candidateLinksEnriched",
        description="Selected links to crawl with additional information",
    )


# --- Pydantic models used only for LLM output parsing ---


class PySearchPrompt(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    search_prompt: str = Field(
        serialization_alias="searchPrompt",
        validation_alias="searchPrompt",
        description="A single string containing the search prompt.",
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
