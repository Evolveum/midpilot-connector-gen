# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import pytest

from src.integrations.web.page_links import PageLink
from src.modules.scrape.core.candidates import (
    STARTER_HOST,
    STARTER_SECTION,
    STARTER_SITE,
    CrawlScope,
    rank_candidate_links,
)

SCOPE = CrawlScope.from_starter_links(
    [
        "https://docs.example.com/api/v3/index.html",
        "https://vendor.github.io/product/",
        "https://github.com/Vendor/product-api",
    ]
)


@pytest.mark.parametrize(
    ("url", "rank"),
    [
        ("https://docs.example.com/api/v3/users", STARTER_SECTION),
        ("https://docs.example.com/api/v3", STARTER_SECTION),
        ("https://docs.example.com/guides/admin", STARTER_HOST),
        ("https://www.example.com/developers", STARTER_SITE),
        ("https://community.example.com/t/users-api", STARTER_SITE),
        ("https://example.org/api", None),
        # A tenant of a shared hosting domain is its own site.
        ("https://vendor.github.io/product/reference", STARTER_SECTION),
        ("https://other.github.io/product/reference", None),
        # On GitHub the scope is the owner of the starter repository.
        ("https://github.com/vendor/other-repo", STARTER_HOST),
        ("https://github.com/someone-else/fork", None),
    ],
)
def test_scope_follows_the_starter_links(url: str, rank: int | None) -> None:
    assert SCOPE.rank(url) == rank


def test_api_descriptions_may_also_come_from_raw_github_files() -> None:
    assert SCOPE.allows_api_description("https://raw.githubusercontent.com/any/repo/main/openapi.yaml")
    assert not SCOPE.allows_api_description("https://other.test/openapi.yaml")


def test_candidates_are_ranked_by_scope_then_idm_words_and_out_of_scope_ones_dropped() -> None:
    links = [
        PageLink("https://www.example.com/company", "Company"),
        PageLink("https://docs.example.com/guides/install", "Installation"),
        PageLink("https://docs.example.com/api/v3/news", "News"),
        PageLink("https://docs.example.com/api/v3/groups", "Group membership"),
        PageLink("https://other.test/api/users", "Users"),
    ]

    assert [link.url for link in rank_candidate_links(links, SCOPE)] == [
        "https://docs.example.com/api/v3/groups",
        "https://docs.example.com/api/v3/news",
        "https://docs.example.com/guides/install",
        "https://www.example.com/company",
    ]
