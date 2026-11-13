# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import patch

import pytest

from src.integrations.web.spec_discovery import discover_spec_urls, looks_like_spec_url, to_raw_file_url

PAGE_URL = "https://developer.example.test/api/reference"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("element", "spec_url"),
    [
        ('<redoc spec-url="/specs/openapi.yaml"></redoc>', "https://developer.example.test/specs/openapi.yaml"),
        ('<rapi-doc spec-url="https://api.example.test/v1/spec"></rapi-doc>', "https://api.example.test/v1/spec"),
        ('<elements-api apiDescriptionUrl="spec.json"></elements-api>', "https://developer.example.test/api/spec.json"),
        ('<script id="api-reference" data-url="/api.json"></script>', "https://developer.example.test/api.json"),
        (
            '<link rel="alternate" type="application/openapi+json" href="/v3/spec">',
            "https://developer.example.test/v3/spec",
        ),
        ("<script>Redoc.init('/docs/api.yaml', {}, el)</script>", "https://developer.example.test/docs/api.yaml"),
        (
            '<a download="cafe.yaml" href="specs/cafe.yaml">Download</a>',
            "https://developer.example.test/api/specs/cafe.yaml",
        ),
    ],
)
async def test_spec_url_of_a_viewer_is_found(element: str, spec_url: str) -> None:
    assert await discover_spec_urls(f"<html><body>{element}</body></html>", PAGE_URL, []) == [spec_url]


@pytest.mark.asyncio
async def test_inline_swagger_ui_configuration_lists_every_spec() -> None:
    html = """<script>
    window.ui = SwaggerUIBundle({
      urls: [{url: "/specs/users.json", name: "Users"}, {url: "/specs/groups.json", name: "Groups"}],
      oauth2RedirectUrl: "https://developer.example.test/oauth2-redirect.html",
      dom_id: "#swagger-ui",
    });
    </script>"""

    assert await discover_spec_urls(html, PAGE_URL, []) == [
        "https://developer.example.test/specs/users.json",
        "https://developer.example.test/specs/groups.json",
    ]


@pytest.mark.asyncio
async def test_swagger_initializer_and_its_config_url_are_followed() -> None:
    configurations = {
        "https://developer.example.test/swagger-ui/swagger-initializer.js?v=5": (
            'window.ui = SwaggerUIBundle({configUrl: "/v3/api-docs/swagger-config", dom_id: "#swagger-ui"});'
        ),
        "https://developer.example.test/v3/api-docs/swagger-config": (
            '{"urls":[{"url":"/v3/api-docs/users","name":"users"}],"validatorUrl":""}'
        ),
    }

    async def fetch_config(_http_session, url: str) -> str | None:
        return configurations.get(url)

    html = '<div id="swagger-ui"></div><script src="/swagger-ui/swagger-initializer.js?v=5"></script>'
    with patch("src.integrations.web.spec_discovery._fetch_config", fetch_config):
        spec_urls = await discover_spec_urls(html, PAGE_URL, [])

    assert spec_urls == ["https://developer.example.test/v3/api-docs/users"]


@pytest.mark.asyncio
async def test_spec_links_are_found_and_github_file_pages_map_to_raw_files() -> None:
    links = [
        "https://developer.example.test/api/users",
        "https://developer.example.test/downloads/openapi.json",
        "https://github.com/example/api/blob/main/spec/swagger.yaml",
    ]

    assert await discover_spec_urls("<html></html>", PAGE_URL, links) == [
        "https://developer.example.test/downloads/openapi.json",
        "https://raw.githubusercontent.com/example/api/main/spec/swagger.yaml",
    ]


@pytest.mark.asyncio
async def test_the_page_itself_is_never_returned() -> None:
    page_url = "https://developer.example.test/openapi.json"

    assert await discover_spec_urls("<html></html>", page_url, [page_url, f"{page_url}/"]) == []


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://x.test/openapi.yaml", True),
        ("https://x.test/static/jira-swagger-v3.json", True),
        ("https://x.test/v3/api-docs", True),
        ("https://x.test/docs/openapi", False),
        ("https://x.test/package.json", False),
    ],
)
def test_spec_url_heuristic(url: str, expected: bool) -> None:
    assert looks_like_spec_url(url) is expected


def test_only_github_file_pages_are_rewritten() -> None:
    assert to_raw_file_url("https://github.com/o/r/tree/main/spec") == "https://github.com/o/r/tree/main/spec"
    assert to_raw_file_url("https://gitlab.test/o/r/blob/main/a.yaml") == "https://gitlab.test/o/r/blob/main/a.yaml"
