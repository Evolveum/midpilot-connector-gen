# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from datetime import timedelta

from pydantic import BaseModel, Field, model_validator


class DigesterSettings(BaseModel):
    """
    Configuration for Digester module.
    """

    digester_input_check_interval: timedelta = Field(
        timedelta(weeks=4),
        description="Time interval for checking if the same digester input has been processed before.",
    )
    max_concurrent_llm_calls: int = Field(
        10,
        ge=1,
        description="Maximum number of concurrent digester LLM calls in app process.",
    )
    chunk_llm_retry_attempts: int = Field(
        2,
        ge=1,
        description="Maximum attempts for transient digester chunk LLM failures.",
    )
    chunk_llm_retry_base_delay_seconds: float = Field(
        1.0,
        ge=0,
        description="Initial backoff delay for transient digester chunk LLM retries.",
    )
    info_metadata_uncertainty_threshold: float = Field(
        0.05,
        description=(
            "Threshold ratio used by digester metadata merge. "
            "Values below this evidence ratio across processed documents are ignored as uncertain."
        ),
    )
    scim_cloud_enabled: bool = Field(
        True,
        description="Enable the scim.cloud registry signal when detecting apiType.",
    )
    scim_cloud_v1_url: str = Field(
        "https://raw.githubusercontent.com/aaronpk/scim.cloud/master/public/json/scim_v1_implementations.json",
        description="Source URL for the scim.cloud SCIM 1.1 implementations list.",
    )
    scim_cloud_v2_url: str = Field(
        "https://raw.githubusercontent.com/aaronpk/scim.cloud/master/public/json/scim_v2_implementations.json",
        description="Source URL for the scim.cloud SCIM 2.0 implementations list.",
    )
    scim_cloud_cache_ttl: timedelta = Field(
        timedelta(hours=24),
        description="How long the fetched scim.cloud registry is cached in-memory before refresh.",
    )
    scim_cloud_fetch_timeout_seconds: float = Field(
        10.0,
        ge=0,
        description="HTTP timeout when fetching the scim.cloud registry.",
    )
    scim_cloud_match_threshold: float = Field(
        0.85,
        ge=0,
        le=1,
        description=(
            "Minimum fuzzy match score (0-1) for an application name to be considered present in the "
            "scim.cloud registry, matched against both product name and developer."
        ),
    )
    apitype_scim_knowledge_enabled: bool = Field(
        True,
        description=(
            "Enable the documentation-free, LLM-knowledge SCIM signal that asks the model whether the "
            "named application is known to support SCIM provisioning."
        ),
    )
    apitype_scim_web_search_enabled: bool = Field(
        True,
        description=(
            "Enable the web-search SCIM signal (Brave/ddgs via the shared search backend) that looks up "
            "SCIM support and availability for the application name. Enabled by default but it performs "
            "an external web search on every metadata extraction."
        ),
    )
    apitype_scim_web_search_query_template: str = Field(
        "{application_name} SCIM provisioning support plan",
        description=(
            "Query template for the web-search SCIM signal. Must contain the '{application_name}' placeholder."
        ),
    )
    apitype_web_search_max_results: int = Field(
        5,
        ge=1,
        description=(
            "Maximum number of web search results fed into a web-search apiType signal LLM call. "
            "Shared by the SCIM and REST web-search signals."
        ),
    )
    apitype_web_search_fetch_pages: bool = Field(
        True,
        description=(
            "Open every web search result page (via the shared crawl4ai scraper) and feed its full content to the "
            "web-search apiType signal LLM instead of only the search snippets. Falls back to the snippet for any "
            "page that cannot be fetched. The number of pages opened equals 'apitype_web_search_max_results'. "
            "Shared by the SCIM and REST web-search signals."
        ),
    )
    apitype_web_search_page_max_chars: int = Field(
        6000,
        ge=0,
        description=(
            "Maximum characters of each fetched page fed into a web-search apiType signal LLM call. "
            "Shared by the SCIM and REST web-search signals."
        ),
    )
    apitype_rest_knowledge_enabled: bool = Field(
        True,
        description=(
            "Enable the documentation-free, LLM-knowledge REST signal that asks the model whether the named "
            "application is known to expose a REST/OpenAPI provisioning API."
        ),
    )
    apitype_rest_web_search_enabled: bool = Field(
        True,
        description=(
            "Enable the web-search REST signal that looks up REST/OpenAPI support and availability for the "
            "application name. Shares the web-search page-fetch settings (max results, fetch pages, page max "
            "chars) with the SCIM web-search signal; only the query template and enable flag are REST-specific."
        ),
    )
    apitype_rest_web_search_query_template: str = Field(
        "{application_name} REST API OpenAPI Swagger documentation",
        description=(
            "Query template for the web-search REST signal. Must contain the '{application_name}' placeholder."
        ),
    )
    fuzzy_start_marker_error_ratio: float = Field(
        0.05,
        description="Allowed fuzzy-match error ratio when validating extracted sequence markers.",
    )
    fuzzy_end_marker_error_ratio: float = Field(
        0.15,
        description="Allowed fuzzy-match error ratio for end sequence markers, where required precision is lower.",
    )
    sequence_max_length: int = Field(
        10000,
        description="Maximum character distance allowed between matched start and end sequence markers.",
    )
    auth_min_documentation_items: int = Field(
        15,
        description="Minimum number of documentation items required to use the default auth criteria; otherwise extended criteria are used.",
    )
    build_from_sequences_step_size: int = Field(
        2,
        ge=1,
        description="Number of sequences to process concurrently in the build_from_sequences function.",
    )
    min_start_sequence_len_attributes: int = Field(
        5,
        description="Minimum length in chars for start sequences when extracting attributes.",
    )
    max_start_sequence_len_attributes: int = Field(
        2000,
        description="Maximum length in chars for start sequences when extracting attributes.",
    )
    min_end_sequence_len_attributes: int = Field(
        5,
        description="Minimum length in chars for end sequences when extracting attributes.",
    )
    max_end_sequence_len_attributes: int = Field(
        2000,
        description="Maximum length in chars for end sequences when extracting attributes.",
    )
    min_start_sequence_len_auth: int = Field(
        10,
        description="Minimum length in chars for start sequences when extracting authentication information.",
    )
    max_start_sequence_len_auth: int = Field(
        2000,
        description="Maximum length in chars for start sequences when extracting authentication information.",
    )
    min_end_sequence_len_auth: int = Field(
        10,
        description="Minimum length in chars for end sequences when extracting authentication information.",
    )
    max_end_sequence_len_auth: int = Field(
        2000,
        description="Maximum length in chars for end sequences when extracting authentication information.",
    )
    marker_word_cutoff_length: int = Field(
        50,
        description="Maximum length of individual words in sequence markers; longer words are truncated to this length to improve performance."
        "This is only applied after fuzzy matching because in regex search, there is a significant drop in performance with a lot of \\s patterns  ",
    )
    relation_generic_attribute_tokens: list[str] = Field(
        default_factory=lambda: [
            "a",
            "an",
            "are",
            "as",
            "by",
            "has",
            "have",
            "id",
            "ids",
            "is",
            "of",
            "ref",
            "refs",
            "reference",
            "references",
            "the",
            "to",
            "via",
            "with",
        ],
        description="Generic relation attribute tokens ignored when collapsing wording-only relation duplicates.",
    )
    attributes_debug_table_log: bool = Field(
        False,
        description="Enable detailed logging of extracted attributes as formatted tables for debugging purposes.",
    )

    @model_validator(mode="after")
    def _validate_apitype_web_search_query_templates(self) -> "DigesterSettings":
        if "{application_name}" not in self.apitype_scim_web_search_query_template:
            raise ValueError("apitype_scim_web_search_query_template must contain the '{application_name}' placeholder")
        if "{application_name}" not in self.apitype_rest_web_search_query_template:
            raise ValueError("apitype_rest_web_search_query_template must contain the '{application_name}' placeholder")
        return self
