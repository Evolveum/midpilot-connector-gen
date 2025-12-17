# Copyright (c) 2025 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.modules.discovery import service
from src.modules.discovery.schema import CandidateLinksInput, PyScrapeFetchReferences, PySearchPrompt


def test_search_with_ddgs():
    """Test the DuckDuckGo search helper function."""
    with patch("src.modules.discovery.service.DDGS") as mock_ddgs:
        mock_instance = MagicMock()
        mock_instance.text.return_value = [{"title": "Test", "href": "https://example.com", "body": "Test body"}]
        mock_ddgs.return_value.__enter__.return_value = mock_instance

        results = service._search_with_ddgs("test query", max_results=1)

        assert len(results) == 1
        assert results[0]["title"] == "Test"
        assert results[0]["href"] == "https://example.com"
        assert results[0]["body"] == "Test body"
        mock_instance.text.assert_called_once_with("test query", max_results=1, backend=["bing", "brave", "yahoo"])


def test_search_web_uses_configured_backend():
    """Test that search_web uses the configured backend."""
    from types import SimpleNamespace

    with (
        patch("src.modules.discovery.service._search_with_ddgs") as mock_ddgs,
        patch("src.modules.discovery.service._search_with_brave") as mock_brave,
        patch("src.modules.discovery.service.config") as mock_config,
    ):
        # ddgs path
        mock_config.search = SimpleNamespace(method_name="ddgs")
        service.search_web("test")
        mock_ddgs.assert_called_once_with("test")

        mock_ddgs.reset_mock()
        mock_brave.reset_mock()

        # brave path (ensure creds exist to avoid early return in real code)
        mock_config.search = SimpleNamespace(method_name="brave")
        mock_config.brave = SimpleNamespace(endpoint="https://example.test/search", api_key="key")
        service.search_web("test")
        mock_brave.assert_called_once_with("test")


def test_fetch_parser_response():
    """Test the fetch_parser_response function."""
    mock_parser_model = MagicMock()

    # Patch OutputFixingParser.from_llm to return an object whose parse()
    # gives a PyScrapeFetchReferences instance (what the function returns).
    with (
        patch("src.modules.discovery.service.OutputFixingParser") as mock_ofp,
        patch("src.modules.discovery.service.PydanticOutputParser"),
    ):
        meta = MagicMock()
        meta.parse.return_value = PyScrapeFetchReferences(name="n", urls_to_crawl=["https://x.y/z"], text_output="txt")
        mock_ofp.from_llm.return_value = meta

        result = service.fetch_parser_response(
            parser_model=mock_parser_model,
            unstructured_output=json.dumps({"any": "payload"}),
            pydantic_class_template=PyScrapeFetchReferences,
        )

        assert isinstance(result, PyScrapeFetchReferences)
        assert result.urls_to_crawl == ["https://x.y/z"]


def test_generate_query_via_llm(mock_llm, mock_llm_eval):
    """Test generating a search query via LLM."""
    mock_llm.return_value.invoke.return_value = AIMessage(
        content=json.dumps({"searchPrompt": "test search query", "searchReasoning": "test reasoning"})
    )

    query, _ = service._generate_query_via_llm(
        model=mock_llm.return_value,
        parser_model=mock_llm_eval.return_value,
        user_prompt="test user prompt",
        system_prompt="test system prompt",
    )

    assert query == "test search query"
    mock_llm.return_value.invoke.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_candidate_links(mock_llm, mock_llm_eval, mock_search_web, mock_discovery_update_job_progress):
    """Test the main fetch_candidate_links function."""

    # Main LLM returns JSON content used later in pipeline
    mock_llm.return_value.invoke.return_value = AIMessage(
        content=json.dumps({"name": "test", "urlsToCrawl": ["https://example.com/1"], "textOutput": "test output"})
    )

    with (
        patch("src.modules.discovery.service.get_default_llm_small1") as mock_llm_small1,
        patch("src.modules.discovery.service.get_default_llm_small2") as mock_llm_small2,
        patch("src.modules.discovery.service.OutputFixingParser") as mock_ofp,
        patch("src.modules.discovery.service.PydanticOutputParser"),
    ):
        # Mock the LLMs that get called inside _run_discovery_blocking
        mock_llm_small1.return_value = mock_llm.return_value
        mock_llm_small2.return_value = mock_llm_eval.return_value

        # First parser: for _generate_query_via_llm -> returns PySearchPrompt
        meta_prompt = MagicMock()
        meta_prompt.parse.return_value = PySearchPrompt(search_prompt="test search query")

        # Second parser: for fetch_parser_response -> returns PyScrapeFetchReferences
        meta_refs = MagicMock()
        meta_refs.parse.return_value = PyScrapeFetchReferences(
            name="test", urls_to_crawl=["https://example.com/1"], text_output="ok"
        )

        # Ensure two distinct returns for two OutputFixingParser.from_llm(...) calls
        mock_ofp.from_llm.side_effect = [meta_prompt, meta_refs]

        input_data = CandidateLinksInput(
            application_name="test-app", application_version="1.0.0", llm_generated_search_query=True
        )

        result = await service.fetch_candidate_links(input_data, "test-job-id")

        assert len(result.candidate_links) > 0
        assert "https://example.com/1" in result.candidate_links
        assert len(result.candidate_links_enriched) > 0
        mock_discovery_update_job_progress.assert_called()
