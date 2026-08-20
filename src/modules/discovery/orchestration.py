# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Request/job orchestration for discovery operations.

Keeps the HTTP router focused on request parsing and session validation while
this module owns job payload assembly, scheduling, and session job-pointer
persistence.
"""

from uuid import UUID

from src.database.repositories.session_repository import SessionRepository
from src.jobs import job_input_reference, schedule_coroutine_job
from src.modules.discovery import service
from src.modules.discovery.schema import CandidateLinksInput


async def schedule_candidate_link_discovery(
    *,
    repo: SessionRepository,
    session_id: UUID,
    request: CandidateLinksInput,
) -> UUID:
    input_payload = request.model_dump(by_alias=True)

    job_id = await schedule_coroutine_job(
        db=repo.db,
        job_type="discovery.getCandidateLinks",
        input_payload=input_payload,
        worker=service.discover_candidate_links,
        worker_args=(job_input_reference(), session_id),
        initial_stage="queue",
        initial_message="Queued candidate links discovery",
        session_id=session_id,
        session_result_key="discoveryOutput",
    )

    await repo.update_session(
        session_id,
        {"discoveryJobId": str(job_id), "discoveryInput": input_payload},
    )

    return job_id
