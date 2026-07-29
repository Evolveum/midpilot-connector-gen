# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from src.database.repositories.job_repository import JobRepository


def _compile_postgres(query):
    return query.compile(dialect=postgresql.dialect())


@pytest.mark.asyncio
async def test_reusable_job_query_is_scoped_to_session_or_matching_owner() -> None:
    db = MagicMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = None
    db.execute = AsyncMock(return_value=result)
    requesting_session_id = uuid4()

    await JobRepository(db).get_job_by_input(
        "discovery.getCandidateLinks",
        {"applicationName": "Demo"},
        datetime.now(timezone.utc) - timedelta(days=1),
        requesting_session_id=requesting_session_id,
    )

    compiled = _compile_postgres(db.execute.await_args.args[0])
    sql = " ".join(str(compiled).split())
    assert "JOIN sessions AS sessions_1 ON sessions_1.session_id = jobs.session_id" in sql
    assert "jobs.session_id = %(session_id_1)s::UUID OR" in sql
    assert "sessions_2.session_id = %(session_id_2)s::UUID" in sql
    # NULL owner must match NULL owner, so ownerless sessions form one tenant.
    assert "sessions_1.api_key_id IS NOT DISTINCT FROM" in sql
    assert list(compiled.params.values()).count(requesting_session_id) == 2


@pytest.mark.asyncio
async def test_session_job_query_constrains_both_job_and_session_ids() -> None:
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)
    job_id = uuid4()
    session_id = uuid4()

    await JobRepository(db).get_job_for_session(job_id, session_id)

    compiled = _compile_postgres(db.execute.await_args.args[0])
    sql = " ".join(str(compiled).split())
    assert "jobs.job_id = %(job_id_1)s::UUID" in sql
    assert "jobs.session_id = %(session_id_1)s::UUID" in sql
    assert job_id in compiled.params.values()
    assert session_id in compiled.params.values()


@pytest.mark.asyncio
async def test_claim_query_keeps_documentation_dependent_jobs_out_of_worker_slots() -> None:
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)

    claimed = await JobRepository(db).claim_next_job(
        worker_id="worker-a",
        claim_timeout_seconds=60,
    )

    assert claimed is None
    compiled = _compile_postgres(db.execute.await_args_list[0].args[0])
    sql = " ".join(str(compiled).split())
    assert "jobs.waits_for_documentation IS false" in sql
    assert "jobs.documentation_wait_until <=" in sql
    assert "NOT (EXISTS (SELECT jobs_1.job_id" in sql
    list_params = [value for value in compiled.params.values() if isinstance(value, list)]
    assert ["scrape.getRelevantDocumentation", "documentation.processUpload"] in list_params


@pytest.mark.asyncio
async def test_progress_update_propagates_database_errors_to_transaction_owner() -> None:
    db = MagicMock()
    db.execute = AsyncMock(side_effect=RuntimeError("serialization failure"))

    with pytest.raises(RuntimeError, match="serialization failure"):
        await JobRepository(db).update_job_progress(uuid4(), stage="queue")


@pytest.mark.asyncio
async def test_update_job_input_distinguishes_a_missing_job_from_a_lost_claim() -> None:
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)
    job_id = uuid4()

    with pytest.raises(FileNotFoundError, match=str(job_id)):
        await JobRepository(db).update_job_input(job_id, {"value": "updated"})
