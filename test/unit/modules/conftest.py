"""Shared test fixtures for all modules."""

# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import sys
from contextlib import ExitStack
from functools import cache
from importlib import import_module
from pkgutil import walk_packages
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_openai import ChatOpenAI

from src.app import api
from src.integrations.web import SearchResult
from src.jobs import update_job_progress

# Common fixtures


@pytest.fixture
def test_client():
    """Return a test client for the FastAPI app."""
    return TestClient(api)


@pytest.fixture
def mock_llm():
    """Default mock LLM for testing."""
    with patch("src.core.llm.get_default_llm") as mock_llm:
        mock_llm.return_value = MagicMock(spec=ChatOpenAI)
        yield mock_llm


@pytest.fixture
def mock_llm_eval():
    """Mock LLM for evaluation."""
    with patch("src.core.llm.get_default_llm") as mock_llm:
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


_DIGESTER_PACKAGE = "src.modules.digester"


@cache
def _digester_update_job_progress_targets() -> tuple[str, ...]:
    """Return a patch target for every digester module that imported ``update_job_progress``.

    ``from src.jobs import update_job_progress`` binds the helper into the importing module,
    so each importer needs its own patch. The list is discovered instead of hardcoded: a
    module missing from a hardcoded list keeps calling the real helper, which writes to the
    configured database and swallows the error, leaving the test green.
    """
    package = import_module(_DIGESTER_PACKAGE)
    for module_info in walk_packages(package.__path__, prefix=f"{_DIGESTER_PACKAGE}."):
        import_module(module_info.name)

    return tuple(
        f"{name}.update_job_progress"
        for name, module in sorted(sys.modules.items())
        if name.startswith(f"{_DIGESTER_PACKAGE}.")
        and getattr(module, "update_job_progress", None) is update_job_progress
    )


@pytest.fixture
def mock_digester_update_job_progress():
    """Mock job progress update across every digester module that reports progress."""
    mock = AsyncMock()
    with ExitStack() as stack:
        for target in _digester_update_job_progress_targets():
            stack.enter_context(patch(target, mock))
        yield mock


@pytest.fixture
def mock_discovery_update_job_progress():
    """Mock job progress update for discovery module."""
    with patch("src.modules.discovery.service.update_job_progress", new_callable=AsyncMock) as mock:
        yield mock
