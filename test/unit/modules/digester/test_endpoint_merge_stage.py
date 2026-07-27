# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.modules.digester.aggregation.merges import merge_endpoint_candidates
from src.modules.digester.schemas import ExtractedEndpointInfo
from src.shared.enums import JobStage


@pytest.mark.asyncio
async def test_merge_endpoint_candidates_does_not_emit_finished_stage():
    """The merge helper must not publish the terminal ``finished`` stage.

    Regression: emitting ``JobStage.finished`` inside the merge helper let a
    poller observe the job as done before the caller attached
    relevantDocumentations and persisted the result, and caused the stage to
    regress (finished -> schema_ready).
    """
    with patch(
        "src.modules.digester.aggregation.merges.update_job_progress",
        new=AsyncMock(),
    ) as mock_update:
        await merge_endpoint_candidates([], "User", uuid4())

    emitted_stages = [call.kwargs.get("stage") for call in mock_update.await_args_list]
    assert JobStage.finished not in emitted_stages


@pytest.mark.asyncio
async def test_merge_endpoint_candidates_merges_editable_fields_from_duplicate_endpoints():
    endpoints = [
        ExtractedEndpointInfo(
            path="/Users",
            method="GET",
            description="List users",
            suggested_use=["getAll"],
        ),
        ExtractedEndpointInfo(
            path="/Users",
            method="GET",
            description="List users with pagination",
            response_content_type="application/scim+json",
            suggested_use=["search"],
        ),
    ]

    merged = await merge_endpoint_candidates(endpoints, "User", uuid4())

    assert len(merged) == 1
    assert merged[0]["description"] == "List users with pagination"
    assert merged[0]["responseContentType"] == "application/scim+json"
    assert merged[0]["suggestedUse"] == ["getAll", "search"]
