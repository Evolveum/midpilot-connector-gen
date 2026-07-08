# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from datetime import timedelta

from pydantic import BaseModel, Field


class ScrapeAndProcessSettings(BaseModel):
    """
    Configuration for Scrape and Process module.
    """

    scrape_input_check_interval: timedelta = Field(
        timedelta(weeks=4), description="Time interval for checking if the same scrape input has been processed before."
    )

    # Scraper controls
    crawl4ai_verbose: bool = Field(
        False,
        description="Enable verbose Crawl4AI console logs (FETCH/SCRAPE/COMPLETE).",
    )
    max_scraper_iterations: int = Field(
        4,
        description="Max outer iterations of the scraper loop",
    )
    max_links_per_documentation: int = Field(
        3000,
        description="Maximum number of raw links to process from a single scraped documentation page",
    )
    irrelevant_links_parts: int = Field(
        5,
        description="Number of parts to split links into for LLM filtering",
    )
    irrelevant_links_parts_min_length: int = Field(
        5,
        description="Minimum number of links in a part for LLM filtering",
    )
    forbidden_url_parts: list[str] = Field(
        [
            "logout",
            "login",
            "signup",
            "register",
            "subscribe",
            "pricing",
            "plans",
            "terms",
            "privacy",
            "contact",
            "about",
            "blog",
            "news",
            "forum",
            "release-notes",
            "changelog",
            "es",
            "pt",
            "de",
            "fr",
            "jp",
            "zh",
            "sk",
            "ru",
            "fr",
            "it",
            "nl",
            "pl",
            "tr",
            "internal",
            "stg",
            "staging",
            "index-all.html",
            "allclasses-index.html",
            "allpackages-index.html",
            "deprecated-list.html",
            "help-doc.html",
        ],
        description="URL substrings to consider irrelevant while scraping",
    )

    # Chunking controls
    chunk_length: int = Field(
        10000,
        description="Max tokens per chunk for LLM processing",
    )
    single_item_schema_max_tokens: int = Field(
        100000,
        gt=0,
        description=(
            "Maximum tokens for a single preserved schema item (SQL/SCIM/conndev) before it is split into "
            "structurally valid sub-schemas. Kept below the LLM context window to leave headroom for the "
            "chunk-processing prompt and completion."
        ),
    )
    max_concurrent: int = Field(
        20,
        description="Max concurrent chunk processing tasks",
    )
    chunk_llm_retry_attempts: int = Field(
        3,
        ge=1,
        description="Maximum attempts for transient chunk-processing LLM failures (e.g. connection errors).",
    )
    chunk_llm_retry_base_delay_seconds: float = Field(
        1.0,
        ge=0,
        description="Initial backoff delay (seconds) for transient chunk-processing LLM retries.",
    )

    chunk_categories: list[str] = Field(
        [
            "spec_yaml",
            "spec_json",
            "reference_api",
            "reference_other",
            "overview",
            "index",
            "tutorial",
            "non-technical",
            "other",
        ],
        description="List of chunk categories to consider while processing, to be used in Literal type",
    )

    latest_version_synonyms: list[str] = Field(
        [
            "latest",
            "current",
            "newest",
            "development",
            "stable",
            "up-to-date",
        ],
        description="List of synonyms indicating latest version in documentation",
    )

    unknown_version_threshold: float = Field(
        0.9,
        description="If the number of chunks with unknown version exceeds this ratio, the app version is considered unknown",
    )

    metadata_uncertainty_threshold: float = Field(
        0.05,
        description="If any parameter in metadata is present in less than this ratio of chunks, it is considered uncertain and ignored",
    )
