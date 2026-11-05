# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Request/job orchestration for scrape operations.

Keeps the HTTP router focused on request parsing and session validation while
this module owns scrape request resolution, job scheduling, and session
job-pointer persistence.
"""

from typing import Any
from uuid import UUID

from src.database.repositories.session_repository import SessionRepository
from src.jobs import job_input_reference, persist_job_pointer, schedule_coroutine_job
from src.modules.scrape import service
from src.modules.scrape.schema import ScrapeJobInput, ScrapeRequest
from src.shared.job_types import JobType
from src.shared.normalize import is_generic_version
from src.shared.session_keys import DISCOVERY, SCRAPE

LATEST_VERSION = "latest"


def _concrete_version(version: Any) -> str | None:
    text = str(version or "").strip()
    return None if is_generic_version(text) else text


async def resolve_scrape_request(
    request: ScrapeRequest,
    repo: SessionRepository,
    session_id: UUID,
) -> ScrapeJobInput:
    """The job input of a scrape request, with its application version and the version the scrape targets.

    ``applicationVersion`` is the requested version, else ``discoveryInput.applicationVersion``, else
    ``"current"``. ``effectiveApplicationVersion`` is the first concrete one of the requested and the
    discovery version, else ``"latest"``: midPoint always requests ``"latest"`` and names the real version
    only in the discovery input. Both are fixed now, so they are part of the cache key.
    """
    explicit_input = request.model_dump(by_alias=True, exclude_unset=True)
    raw_explicit_version = explicit_input.get("applicationVersion")
    explicit_version = raw_explicit_version.strip() if isinstance(raw_explicit_version, str) else ""

    discovery_version = ""
    if _concrete_version(explicit_version) is None:
        discovery_input = await repo.get_session_data(session_id, DISCOVERY.input) or {}
        if isinstance(discovery_input, dict):
            discovery_version = str(discovery_input.get("applicationVersion") or "").strip()

    application_version = (
        explicit_version or discovery_version or str(request.application_version or "").strip() or "current"
    )
    effective_version = _concrete_version(explicit_version) or _concrete_version(discovery_version) or LATEST_VERSION
    return ScrapeJobInput.model_validate(
        {
            **request.model_dump(),
            "application_version": application_version,
            "effective_application_version": effective_version,
        }
    )


async def schedule_scrape_documentation(
    *,
    repo: SessionRepository,
    session_id: UUID,
    request: ScrapeRequest,
) -> UUID:
    resolved_request = await resolve_scrape_request(request, repo, session_id)
    input_payload = resolved_request.model_dump(by_alias=True)

    job_id = await schedule_coroutine_job(
        db=repo.db,
        job_type=JobType.SCRAPE_RELEVANT_DOCUMENTATION,
        input_payload=input_payload,
        worker=service.fetch_relevant_documentation,
        worker_args=(job_input_reference(), session_id),
        initial_stage="queue",
        initial_message="Queued scraping job",
        session_id=session_id,
        session_result_key=SCRAPE.output,
    )

    await persist_job_pointer(repo, session_id, SCRAPE, input_payload, job_id)

    return job_id
