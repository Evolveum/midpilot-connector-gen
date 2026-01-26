"""Shared test fixtures for all modules."""

# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_openai import ChatOpenAI

from src.app import api

# Common fixtures


@pytest.fixture
def test_client():
    """Return a test client for the FastAPI app."""
    return TestClient(api)


@pytest.fixture
def mock_llm():
    """Default mock LLM for testing."""
    with patch("src.common.llm.get_default_llm") as mock_llm:
        mock_llm.return_value = MagicMock(spec=ChatOpenAI)
        yield mock_llm


@pytest.fixture
def mock_llm_eval():
    """Mock LLM for evaluation."""
    with patch("src.common.llm.get_default_llm_small2") as mock_llm:
        mock_llm.return_value = MagicMock(spec=ChatOpenAI)
        yield mock_llm


@pytest.fixture
def mock_search_web():
    """Mock the web search functionality for discovery tests."""
    with patch("src.modules.discovery.service.search_web") as mock:
        mock.return_value = [
            {"title": "Test Title 1", "href": "https://example.com/1", "body": "Test body 1", "source": "test"},
            {"title": "Test Title 2", "href": "https://example.com/2", "body": "Test body 2", "source": "test"},
        ]
        yield mock


@pytest.fixture
def mock_digester_update_job_progress():
    """Mock job progress update for digester module."""
    with patch("src.modules.digester.service.update_job_progress") as mock:
        yield mock


@pytest.fixture
def mock_discovery_update_job_progress():
    """Mock job progress update for discovery module."""
    with patch("src.modules.discovery.service.update_job_progress") as mock:
        yield mock
