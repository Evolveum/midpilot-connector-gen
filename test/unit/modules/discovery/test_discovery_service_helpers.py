# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage

import src.integrations.web.search as search
import src.modules.discovery.utils.llm_helpers as llm_helpers
from src.modules.discovery.schema import PySearchPrompts


def test_search_with_ddgs():
    """Test the DuckDuckGo search helper function."""
    with patch("src.integrations.web.search.DDGS") as mock_ddgs:
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
        patch("src.integrations.web.search.search_with_ddgs") as mock_ddgs,
        patch("src.integrations.web.search.search_with_brave") as mock_brave,
        patch("src.integrations.web.search.config") as mock_config,
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
    assert parsed.search_prompts[0] == "test search query 1"
    mock_llm.return_value.invoke.assert_called_once()


def test_generate_queries_via_preset_uses_available_templates_up_to_num_queries():
    templates = [
        "{app} REST API documentation {version}",
        "{app} SCIM 2.0 documentation {version}",
    ]
    queries, _, parsed = llm_helpers.generate_queries_via_preset(
        app="LiteLLM",
        version="latest",
        num_queries=8,
        templates=templates,
    )

    assert len(queries) == 2
    assert len(set(queries)) == 2
    assert queries[0] == "LiteLLM REST API documentation latest"
    assert queries[1] == "LiteLLM SCIM 2.0 documentation latest"
    assert parsed.search_prompts == queries


def test_generate_query_via_llm_adds_fallback_templates_when_llm_returns_too_few(mock_llm, mock_llm_eval):
    mock_llm.return_value.invoke.return_value = AIMessage(content=json.dumps({"searchPrompts": ["query from llm"]}))

    with (
        patch("src.modules.discovery.utils.llm_helpers.OutputFixingParser") as mock_ofp,
        patch("src.modules.discovery.utils.llm_helpers.PydanticOutputParser"),
    ):
        meta = MagicMock()
        meta.parse.return_value = PySearchPrompts(search_prompts=["query from llm"])
        mock_ofp.from_llm.return_value = meta

        queries, _, _ = llm_helpers.generate_queries_via_llm(
            model=mock_llm.return_value,
            parser_model=mock_llm_eval.return_value,
            user_prompt="test user prompt",
            system_prompt="test system prompt",
            num_queries=3,
            fallback_templates=[
                "{app} REST API documentation {version}",
                "{app} SCIM 2.0 documentation {version}",
            ],
        )

    assert len(queries) == 3
    assert queries[0] == "query from llm"
    assert "{app} REST API documentation {version}" in queries
    assert "{app} SCIM 2.0 documentation {version}" in queries
