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
