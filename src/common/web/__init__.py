# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Shared web search and fetching infrastructure."""

from src.common.web.fetch import (
    fetch_data_documentation,
    fetch_markdown_pages,
    get_all_content_types,
    get_content_type,
    scrape_all_data_documentations,
    scrape_urls,
)
from src.common.web.schemas import SearchResult
from src.common.web.search import (
    normalize_result,
    search_web,
    search_with_brave,
    search_with_ddgs,
)

__all__ = [
    "SearchResult",
    "fetch_data_documentation",
    "fetch_markdown_pages",
    "get_all_content_types",
    "get_content_type",
    "normalize_result",
    "scrape_all_data_documentations",
    "scrape_urls",
    "search_web",
    "search_with_brave",
    "search_with_ddgs",
]
