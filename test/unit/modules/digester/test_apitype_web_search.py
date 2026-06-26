# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, MagicMock, patch

import pydantic
import pytest

from src.common.enums import ApiType, ScimAvailability
from src.common.web import SearchResult
from src.config import DigesterSettings
from src.modules.digester.extractors.apitype import web_search
from src.modules.digester.extractors.apitype.web_search import lookup_api_type_web_search
from src.modules.digester.schemas import ApiTypeSignalResult


def _enable_web_search(mock_config: MagicMock, *, fetch_pages: bool = False) -> None:
    mock_config.digester.apitype_web_search_enabled = True
    mock_config.digester.apitype_web_search_max_results = 5
    mock_config.digester.apitype_web_search_query_template = "{application_name} SCIM provisioning support plan"
    mock_config.digester.apitype_web_search_fetch_pages = fetch_pages
    mock_config.digester.apitype_web_search_page_max_chars = 6000


def _results() -> list[SearchResult]:
    return [
        SearchResult(
            title="Slack SCIM API",
            href="https://api.slack.com/scim",
            body="Slack supports SCIM provisioning on Enterprise Grid.",
            source="brave",
        )
    ]


@pytest.mark.asyncio
async def test_disabled_skips_search_and_llm():
    with (
        patch("src.modules.digester.extractors.apitype.web_search.config") as mock_config,
        patch("src.modules.digester.extractors.apitype.web_search.search_web") as mock_search,
        patch("src.modules.digester.extractors.apitype.web_search.invoke_llm", new_callable=AsyncMock) as mock_invoke,
    ):
        mock_config.digester.apitype_web_search_enabled = False
        result = await lookup_api_type_web_search("Slack")

    assert result.supports_scim is False
    mock_search.assert_not_called()
    mock_invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_name_skips_search():
    with (
        patch("src.modules.digester.extractors.apitype.web_search.config") as mock_config,
        patch("src.modules.digester.extractors.apitype.web_search.search_web") as mock_search,
    ):
        _enable_web_search(mock_config)
        result = await lookup_api_type_web_search("   ")

    assert result.supports_scim is False
    mock_search.assert_not_called()


@pytest.mark.asyncio
async def test_no_results_skips_llm():
    with (
        patch("src.modules.digester.extractors.apitype.web_search.config") as mock_config,
        patch("src.modules.digester.extractors.apitype.web_search.search_web", return_value=[]) as mock_search,
        patch("src.modules.digester.extractors.apitype.web_search.invoke_llm", new_callable=AsyncMock) as mock_invoke,
    ):
        _enable_web_search(mock_config)
        result = await lookup_api_type_web_search("Totally Unknown App")

    assert result.supports_scim is False
    mock_search.assert_called_once()
    mock_invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_returns_classified_scim_from_results():
    response = ApiTypeSignalResult(
        supports_scim=True,
        api_type=[ApiType.SCIM],
        scim_availability=ScimAvailability.PAID,
        required_plan="Enterprise Grid",
    )
    with (
        patch("src.modules.digester.extractors.apitype.web_search.config") as mock_config,
        patch("src.modules.digester.extractors.apitype.web_search.search_web", return_value=_results()) as mock_search,
        patch(
            "src.modules.digester.extractors.apitype.web_search.invoke_llm",
            new_callable=AsyncMock,
            return_value=response,
        ) as mock_invoke,
    ):
        _enable_web_search(mock_config)
        result = await lookup_api_type_web_search("Slack")

    assert result.supports_scim is True
    assert result.scim_availability is ScimAvailability.PAID
    assert result.required_plan == "Enterprise Grid"
    # The blocking search backend must be reused and the LLM invoked with the results.
    mock_search.assert_called_once()
    mock_invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_failure_is_graceful():
    with (
        patch("src.modules.digester.extractors.apitype.web_search.config") as mock_config,
        patch("src.modules.digester.extractors.apitype.web_search.search_web", side_effect=RuntimeError("brave down")),
        patch("src.modules.digester.extractors.apitype.web_search.invoke_llm", new_callable=AsyncMock) as mock_invoke,
    ):
        _enable_web_search(mock_config)
        result = await lookup_api_type_web_search("Slack")

    assert result.supports_scim is False
    mock_invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_llm_failure_is_graceful():
    with (
        patch("src.modules.digester.extractors.apitype.web_search.config") as mock_config,
        patch("src.modules.digester.extractors.apitype.web_search.search_web", return_value=_results()),
        patch(
            "src.modules.digester.extractors.apitype.web_search.invoke_llm",
            new_callable=AsyncMock,
            side_effect=RuntimeError("llm down"),
        ),
    ):
        _enable_web_search(mock_config)
        result = await lookup_api_type_web_search("Slack")

    assert result.supports_scim is False


def test_query_template_without_placeholder_is_rejected():
    # A misconfigured template must fail fast at config load, not at request time.
    with pytest.raises(pydantic.ValidationError):
        DigesterSettings(apitype_web_search_query_template="no placeholder here")


@pytest.mark.asyncio
async def test_malformed_query_template_degrades_gracefully():
    with (
        patch("src.modules.digester.extractors.apitype.web_search.config") as mock_config,
        patch("src.modules.digester.extractors.apitype.web_search.search_web") as mock_search,
    ):
        _enable_web_search(mock_config)
        mock_config.digester.apitype_web_search_query_template = "{application_name} {unexpected}"
        result = await lookup_api_type_web_search("Slack")

    assert result.supports_scim is False
    mock_search.assert_not_called()


def test_format_search_results_truncates_long_snippets():
    long_body = "x" * (web_search._MAX_SNIPPET_CHARS + 50)
    formatted = web_search._format_search_results(
        [SearchResult(title="T", href="https://e.com", body=long_body, source="brave")],
        page_contents={},
        page_max_chars=6000,
    )
    assert "https://e.com" in formatted
    assert "SNIPPET:" in formatted
    assert "…" in formatted
    assert len(formatted) < len(long_body) + 100


def test_format_uses_full_page_when_available():
    results = [SearchResult(title="T", href="https://e.com/scim/", body="short snippet", source="brave")]
    # Matching is by normalized URL (trailing slash stripped).
    page_contents = {"https://e.com/scim": "FULL PAGE about SCIM provisioning on Enterprise plan."}
    formatted = web_search._format_search_results(results, page_contents=page_contents, page_max_chars=6000)
    assert "PAGE CONTENT:" in formatted
    assert "FULL PAGE about SCIM" in formatted
    assert "short snippet" not in formatted


def test_format_truncates_long_page_content():
    long_page = "y" * 7000
    results = [SearchResult(title="T", href="https://e.com", body="snippet", source="brave")]
    formatted = web_search._format_search_results(
        results, page_contents={"https://e.com": long_page}, page_max_chars=6000
    )
    assert "PAGE CONTENT:" in formatted
    assert "…" in formatted
    assert len(formatted) < 6200


@pytest.mark.asyncio
async def test_fetches_pages_and_feeds_full_content_to_llm():
    captured: dict = {}

    async def fake_invoke(chain, payload, **kwargs):
        captured["search_results"] = payload["search_results"]
        return ApiTypeSignalResult(supports_scim=True, api_type=[ApiType.SCIM])

    with (
        patch("src.modules.digester.extractors.apitype.web_search.config") as mock_config,
        patch("src.modules.digester.extractors.apitype.web_search.search_web", return_value=_results()) as mock_search,
        patch(
            "src.modules.digester.extractors.apitype.web_search.fetch_markdown_pages",
            new_callable=AsyncMock,
            return_value={"https://api.slack.com/scim": "FULL PAGE: Slack SCIM on Enterprise Grid."},
        ) as mock_fetch,
        patch("src.modules.digester.extractors.apitype.web_search.invoke_llm", side_effect=fake_invoke),
    ):
        _enable_web_search(mock_config, fetch_pages=True)
        result = await lookup_api_type_web_search("Slack")

    assert result.supports_scim is True
    mock_search.assert_called_once()
    mock_fetch.assert_awaited_once_with(
        ["https://api.slack.com/scim"], logger_prefix=web_search._LOG_PREFIX, log=web_search.logger
    )
    # The LLM must receive the fetched full page, not just the snippet.
    assert "PAGE CONTENT:" in captured["search_results"]
    assert "FULL PAGE: Slack SCIM on Enterprise Grid." in captured["search_results"]
