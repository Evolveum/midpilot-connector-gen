# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.jobs.errors import JobNotFoundError
from src.session.access import resolve_session_job_id


@pytest.mark.asyncio
async def test_explicit_job_id_must_belong_to_path_session() -> None:
    session_id = uuid4()
    foreign_job_id = uuid4()
    session_repo = MagicMock()
    session_repo.db = MagicMock()
    session_repo.get_session_data = AsyncMock()
    job_repo = MagicMock()
    job_repo.get_job_for_session = AsyncMock(return_value=None)

    with patch("src.session.access.JobRepository", return_value=job_repo):
        with pytest.raises(JobNotFoundError):
            await resolve_session_job_id(
                session_repo,
                session_id,
                foreign_job_id,
                session_key="discoveryJobId",
                job_label="discovery",
            )

    job_repo.get_job_for_session.assert_awaited_once_with(foreign_job_id, session_id)
    session_repo.get_session_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_job_id_is_returned_when_it_belongs_to_path_session() -> None:
    session_id = uuid4()
    job_id = uuid4()
    session_repo = MagicMock()
    session_repo.db = MagicMock()
    job_repo = MagicMock()
    job_repo.get_job_for_session = AsyncMock(return_value=SimpleNamespace(job_id=job_id))

    with patch("src.session.access.JobRepository", return_value=job_repo):
        resolved_job_id = await resolve_session_job_id(
            session_repo,
            session_id,
            job_id,
            session_key="discoveryJobId",
            job_label="discovery",
        )

    assert resolved_job_id == job_id
    job_repo.get_job_for_session.assert_awaited_once_with(job_id, session_id)
