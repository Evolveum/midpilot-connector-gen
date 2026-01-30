# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

import src.modules.discovery.core.search as search
import src.modules.discovery.utils.llm_helpers as llm_helpers
from src.modules.discovery import service
from src.modules.discovery.schema import CandidateLinksInput, PyScrapeFetchReferences, PySearchPrompts


def test_search_with_ddgs():
    """Test the DuckDuckGo search helper function."""
    with patch("src.modules.discovery.core.search.DDGS") as mock_ddgs:
        mock_instance = MagicMock()
        mock_instance.text.return_value = [{"title": "Test", "href": "https://example.com", "body": "Test body"}]
        mock_ddgs.return_value.__enter__.return_value = mock_instance

        results = search.search_with_ddgs("test query", max_results=1)

        assert len(results) == 1
        assert results[0].title == "Test"
        assert results[0].href == "https://example.com"
        assert results[0].body == "Test body"
        mock_instance.text.assert_called_once_with("test query", max_results=1, backend=["bing", "brave", "yahoo"])


def test_search_web_uses_configured_backend():
    """Test that search_web uses the configured backend."""
    from types import SimpleNamespace

    with (
        patch("src.modules.discovery.core.search.search_with_ddgs") as mock_ddgs,
        patch("src.modules.discovery.core.search.search_with_brave") as mock_brave,
        patch("src.modules.discovery.core.search.config") as mock_config,
    ):
        # ddgs path
        mock_config.search = SimpleNamespace(method_name="ddgs")
        search.search_web("test")
        mock_ddgs.assert_called_once_with("test", max_results=10)

        mock_ddgs.reset_mock()
        mock_brave.reset_mock()

        # brave path (ensure creds exist to avoid early return in real code)
        mock_config.search = SimpleNamespace(method_name="brave")
        mock_config.brave = SimpleNamespace(endpoint="https://example.test/search", api_key="key")
        search.search_web("test")
        mock_brave.assert_called_once_with("test", max_results=10)


def test_fetch_parser_response():
    """Test the fetch_parser_response function."""
    mock_parser_model = MagicMock()

    # Patch OutputFixingParser.from_llm to return an object whose parse()
    # gives a PyScrapeFetchReferences instance (what the function returns).
    with (
        patch("src.modules.discovery.utils.llm_helpers.OutputFixingParser") as mock_ofp,
        patch("src.modules.discovery.utils.llm_helpers.PydanticOutputParser"),
    ):
        meta = MagicMock()
        meta.parse.return_value = PyScrapeFetchReferences(name="n", urls_to_crawl=["https://x.y/z"], text_output="txt")
        mock_ofp.from_llm.return_value = meta

        result = llm_helpers.fetch_parser_response(
            parser_model=mock_parser_model,
            unstructured_output=json.dumps({"any": "payload"}),
            pydantic_class_template=PyScrapeFetchReferences,
        )

        assert isinstance(result, PyScrapeFetchReferences)
        assert result.urls_to_crawl == ["https://x.y/z"]


def test_generate_query_via_llm(mock_llm, mock_llm_eval):
    """Test generating multiple search queries via LLM."""
    mock_llm.return_value.invoke.return_value = AIMessage(
        content=json.dumps({"searchPrompts": ["test search query 1", "test search query 2", "test search query 3"]})
    )

    queries, _, parsed = llm_helpers.generate_queries_via_llm(
        model=mock_llm.return_value,
        parser_model=mock_llm_eval.return_value,
        user_prompt="test user prompt",
        system_prompt="test system prompt",
        num_queries=3,
    )

    assert len(queries) == 3
    assert "test search query 1" in queries
    mock_llm.return_value.invoke.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_candidate_links(mock_llm, mock_llm_eval, mock_search_web, mock_discovery_update_job_progress):
    """Test the main fetch_candidate_links function."""

    # Main LLM returns JSON content used later in pipeline
    mock_llm.return_value.invoke.return_value = AIMessage(
        content=json.dumps({"name": "test", "urlsToCrawl": ["https://example.com/1"], "textOutput": "test output"})
    )

    with (
        patch("src.modules.discovery.service.get_default_llm") as mock_llm_default,
        patch("src.modules.discovery.utils.llm_helpers.OutputFixingParser") as mock_ofp,
        patch("src.modules.discovery.utils.llm_helpers.PydanticOutputParser"),
    ):
        # Mock the LLMs that get called inside _run_discovery_blocking
        mock_llm_default.return_value = mock_llm.return_value

        # First parser: for _generate_query_via_llm -> returns PySearchPrompt
        meta_prompt = MagicMock()
        meta_prompt.parse.return_value = PySearchPrompts(
            search_prompts=["test search query 1", "test search query 2", "test search query 3"]
        )

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

        result = await service.fetch_candidate_links(input_data, uuid4())

        assert len(result.candidate_links) > 0
        assert "https://example.com/1" in result.candidate_links
        assert len(result.candidate_links_enriched) > 0
        mock_discovery_update_job_progress.assert_called()
