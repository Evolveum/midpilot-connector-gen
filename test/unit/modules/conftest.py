"""Shared test fixtures for all modules."""

# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_openai import ChatOpenAI

from src.app import api
from src.common.web import SearchResult

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
    with patch("src.common.llm.get_default_llm") as mock_llm:
        mock_llm.return_value = MagicMock(spec=ChatOpenAI)
        yield mock_llm


@pytest.fixture
def mock_search_web():
    """Mock the web search functionality for discovery tests."""
    with patch("src.modules.discovery.utils.discovery_helpers.search_web") as mock:
        mock.return_value = [
            SearchResult(title="Test Title 1", href="https://example.com/1", body="Test body 1", source="test"),
            SearchResult(title="Test Title 2", href="https://example.com/2", body="Test body 2", source="test"),
        ]
        yield mock


# Digester workflows live in the extractor modules, so job-progress must be patched
# in each module namespace that calls it. A single shared mock keeps assert_awaited()
# working regardless of which module a workflow ended up in.
_DIGESTER_UPDATE_JOB_PROGRESS_TARGETS = (
    "src.modules.digester.extractors.auth.update_job_progress",
    "src.modules.digester.extractors.connectivity_endpoint.update_job_progress",
    "src.modules.digester.extractors.endpoints.update_job_progress",
    "src.modules.digester.extractors.info.update_job_progress",
    "src.modules.digester.extractors.rest.relations.update_job_progress",
)


@pytest.fixture
def mock_digester_update_job_progress():
    """Mock job progress update across digester modules (workflows live in extractors/)."""
    mock = AsyncMock()
    with ExitStack() as stack:
        for target in _DIGESTER_UPDATE_JOB_PROGRESS_TARGETS:
            stack.enter_context(patch(target, mock))
        yield mock


@pytest.fixture
def mock_discovery_update_job_progress():
    """Mock job progress update for discovery module."""
    with patch("src.modules.discovery.service.update_job_progress", new_callable=AsyncMock) as mock:
        yield mock
