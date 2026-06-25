# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.common import jobs


class _AsyncSessionContext:
    async def __aenter__(self):
        return MagicMock()

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_schedule_coroutine_job_records_session_persistence_failure():
    job_id = uuid4()
    session_id = uuid4()
    finished = asyncio.Event()

    async def worker():
        return {"result": {"value": "ok"}}

    async def set_finished(job_id_arg, result):
        finished.set()
        return {}

    repo = MagicMock()
    repo.update_session = AsyncMock(side_effect=RuntimeError("database write failed"))

    with (
        patch("src.common.jobs.lifecycle.create_job", new_callable=AsyncMock, return_value=job_id),
        patch("src.common.jobs.lifecycle.set_running", new_callable=AsyncMock),
        patch(
            "src.common.jobs.lifecycle.set_finished", new_callable=AsyncMock, side_effect=set_finished
        ) as mock_set_finished,
        patch("src.common.jobs.lifecycle._append_job_error_now", new_callable=AsyncMock) as append_error,
        patch(
            "src.common.jobs.session_persistence.async_session_maker", MagicMock(return_value=_AsyncSessionContext())
        ),
        patch("src.common.jobs.session_persistence.SessionRepository", MagicMock(return_value=repo)),
    ):
        returned_job_id = await jobs.schedule_coroutine_job(
            job_type="digester.test",
            input_payload={"skipCache": True},
            worker=worker,
            session_id=session_id,
            session_result_key="testOutput",
        )
        await asyncio.wait_for(finished.wait(), timeout=1)

    jobs._job_futures.pop(job_id, None)

    assert returned_job_id == job_id
    append_error.assert_awaited_once()
    error_message = append_error.await_args.args[1]
    assert "Session persistence failed" in error_message
    assert "database write failed" in error_message
    mock_set_finished.assert_awaited_once()
