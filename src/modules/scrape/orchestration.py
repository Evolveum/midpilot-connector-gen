# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Request/job orchestration for scrape operations.

Keeps the HTTP router focused on request parsing and session validation while
this module owns scrape request resolution, job scheduling, and session
job-pointer persistence.
"""

from uuid import UUID

from src.common.database.repositories.session_repository import SessionRepository
from src.common.jobs import schedule_coroutine_job
from src.modules.scrape import service
from src.modules.scrape.schema import ScrapeRequest


async def resolve_scrape_request(
    request: ScrapeRequest,
    repo: SessionRepository,
    session_id: UUID,
) -> ScrapeRequest:
    explicit_input = request.model_dump(by_alias=True, exclude_unset=True)
    explicit_version = explicit_input.get("applicationVersion")

    if isinstance(explicit_version, str) and explicit_version.strip():
        return request.model_copy(update={"application_version": explicit_version.strip()})

    discovery_input = await repo.get_session_data(session_id, "discoveryInput") or {}
    if isinstance(discovery_input, dict):
        discovery_version = str(discovery_input.get("applicationVersion") or "").strip()
        if discovery_version:
            return request.model_copy(update={"application_version": discovery_version})

    current_version = str(request.application_version or "").strip()
    if current_version:
        return request.model_copy(update={"application_version": current_version})

    return request.model_copy(update={"application_version": "current"})


async def schedule_scrape_documentation(
    *,
    repo: SessionRepository,
    session_id: UUID,
    request: ScrapeRequest,
) -> UUID:
    resolved_request = await resolve_scrape_request(request, repo, session_id)
    input_payload = resolved_request.model_dump(by_alias=True)

    job_id = await schedule_coroutine_job(
        job_type="scrape.getRelevantDocumentation",
        input_payload=input_payload,
        worker=service.fetch_relevant_documentation,
        worker_args=(resolved_request, session_id),
        initial_stage="queue",
        initial_message="Queued scraping job",
        session_id=session_id,
        session_result_key="scrapeOutput",
    )

    await repo.update_session(
        session_id,
        {
            "scrapeJobId": str(job_id),
            "scrapeInput": input_payload,
        },
    )

    return job_id
