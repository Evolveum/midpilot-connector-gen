# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.common.enums import JobStage
from src.modules.digester.aggregation.merges import merge_endpoint_candidates


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
