# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import pytest

from src.integrations.web.urls import canonicalize_url, is_forbidden_url

FORBIDDEN = ["contact", "register", "news", "blog"]


@pytest.mark.parametrize(
    "url",
    [
        "https://developer.example.com/api/contacts",
        "https://developer.example.com/docs/app-registration",
        "https://developer.example.com/api/newsletters/list",
        "https://developer.example.com/reference/blogPosts",
    ],
)
def test_segment_matching_keeps_api_pages_that_only_contain_a_forbidden_word(url: str) -> None:
    assert is_forbidden_url(url, FORBIDDEN) is False


@pytest.mark.parametrize(
    "url",
    [
        "https://www.example.com/contact",
        "https://www.example.com/blog/2024/release",
        "https://www.example.com/register/",
        "https://www.example.com/company/news",
    ],
)
def test_segment_matching_rejects_forbidden_segments(url: str) -> None:
    assert is_forbidden_url(url, FORBIDDEN) is True


def test_canonicalize_url_drops_fragment_and_tracking_parameters() -> None:
    assert (
        canonicalize_url(" https://docs.example.com/api?utm_source=x&page=2&gclid=abc#users ")
        == "https://docs.example.com/api?page=2"
    )
