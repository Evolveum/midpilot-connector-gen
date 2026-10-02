# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from pydantic import BaseModel

from src.api.responses import build_typed_job_status_response
from src.shared.enums import JobStatus


class ExampleResult(BaseModel):
    value: str


@pytest.mark.asyncio
async def test_build_typed_job_status_response_validates_with_the_declared_model():
    job_id = uuid4()
    status = {
        "jobId": job_id,
        "status": JobStatus.finished.value,
        "result": {"result": {"value": "generated"}},
    }

    with patch("src.api.responses.get_job_status", new_callable=AsyncMock, return_value=status):
        response = await build_typed_job_status_response(job_id, ExampleResult)

    assert response.status is JobStatus.finished
    assert response.result == ExampleResult(value="generated")


@pytest.mark.asyncio
async def test_build_typed_job_status_response_keeps_timestamps_for_a_corrupted_result():
    """A corrupted payload must be reported with the same fields as any other failure,
    so a caller cannot tell the two apart by which fields went missing."""
    job_id = uuid4()
    status = {
        "jobId": job_id,
        "status": JobStatus.finished.value,
        "createdAt": "2026-09-04T10:00:00Z",
        "startedAt": "2026-09-04T10:00:01Z",
        "updatedAt": "2026-09-04T10:00:09Z",
        "progress": {"stage": "finished", "message": "done"},
        "result": {"result": {"unexpected": "shape"}},
    }

    with patch("src.api.responses.get_job_status", new_callable=AsyncMock, return_value=status):
        response = await build_typed_job_status_response(job_id, ExampleResult)

    assert response.status is JobStatus.failed
    assert response.createdAt == status["createdAt"]
    assert response.startedAt == status["startedAt"]
    assert response.updatedAt == status["updatedAt"]
    assert response.progress is not None
    assert (response.progress.stage, response.progress.message) == ("finished", "done")
    assert response.errors and "Corrupted result payload" in response.errors[0]
