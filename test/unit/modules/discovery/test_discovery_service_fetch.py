# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from src.modules.discovery import service
from src.modules.discovery.schema import CandidateLinksInput, PySearchPrompts


@pytest.mark.asyncio
async def test_discover_candidate_links(mock_llm, mock_llm_eval, mock_search_web, mock_discovery_update_job_progress):
    """Test the main discover_candidate_links function."""

    # Main LLM returns JSON content used later in pipeline
    mock_llm.return_value.invoke.return_value = AIMessage(
        content=json.dumps({"name": "test", "urlsToCrawl": ["https://example.com/1"], "textOutput": "test output"})
    )

    with (
        patch("src.modules.discovery.utils.discovery_helpers.get_default_llm") as mock_llm_default,
        patch("src.modules.discovery.utils.llm_helpers.OutputFixingParser") as mock_ofp,
        patch("src.modules.discovery.utils.llm_helpers.PydanticOutputParser"),
    ):
        # Mock the LLMs that get called inside _run_discovery_blocking
        mock_llm_default.return_value = mock_llm.return_value

        # Parser for the generated search queries.
        meta_prompt = MagicMock()
        meta_prompt.parse.return_value = PySearchPrompts(
            search_prompts=["test search query 1", "test search query 2", "test search query 3"]
        )
        mock_ofp.from_llm.return_value = meta_prompt

        input_data = CandidateLinksInput(
            application_name="test-app",
            application_version="1.0.0",
            llm_generated_search_query=True,
            enable_link_filtering=False,
        )

        result = await service.discover_candidate_links(input_data, job_id=uuid4())

        assert len(result.candidate_links) > 0
        assert "https://example.com/1" in result.candidate_links
        assert len(result.candidate_links_enriched) > 0
        mock_discovery_update_job_progress.assert_called()
