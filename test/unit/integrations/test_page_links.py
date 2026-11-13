# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import pytest

from src.integrations.web.page_links import PageLink, page_links
from src.integrations.web.urls import site_of

PAGE_URL = "https://docs.example.test/api/"


def test_page_links_are_normalized_deduplicated_and_keep_the_first_link_text() -> None:
    links = {
        "internal": [
            {"href": "users/", "text": ""},
            {"href": "https://docs.example.test/api/users#list", "text": "  List\n users "},
            {"href": "/api/groups?utm_source=nav&page=2", "title": "Groups"},
            {"href": "https://docs.example.test/api", "text": "This page"},
            {"href": "/assets/logo.svg", "text": "Logo"},
            {"href": "mailto:support@example.test", "text": "Support"},
            {"text": "No href"},
        ],
        "external": [{"href": "https://other.test/docs/", "text": "Other"}],
    }

    assert page_links(links, PAGE_URL) == [
        PageLink("https://docs.example.test/api/users", "List users"),
        PageLink("https://docs.example.test/api/groups?page=2", "Groups"),
        PageLink("https://other.test/docs", "Other"),
    ]
    assert page_links(None, PAGE_URL) == []


@pytest.mark.parametrize(
    ("url", "site"),
    [
        ("https://docs.oracle.com/en/cloud", "oracle.com"),
        ("https://www.example.co.uk/api", "example.co.uk"),
        ("https://vendor.github.io/docs", "vendor.github.io"),
        ("https://acme.atlassian.net/wiki", "acme.atlassian.net"),
        ("not a url", ""),
    ],
)
def test_site_of_a_tenant_of_shared_hosting_is_its_exact_host(url: str, site: str) -> None:
    assert site_of(url) == site
