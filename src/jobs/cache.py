# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Output reuse (caching) for jobs.

Before running a worker, look for a recent finished job of the same type with the same
normalized input and, when possible, reuse its result instead of recomputing it. The job
type's declared :class:`~src.shared.job_types.JobCachePolicy` decides how old the source
may be and how its output becomes this job's result; nothing is inferred from the type
name or from the shape of the cached payload. When the previous output cannot be reused,
the worker runs via the provided ``run_normal_worker`` callback.
"""

import copy
import logging
from datetime import timedelta
from typing import Any, Awaitable, Callable, Dict, List, Sequence
from uuid import UUID, uuid4

from pydantic import ValidationError

from src.config import config
from src.core.db import async_session_maker
from src.database.models import Job
from src.database.repositories.documentation_repository import DocumentationRepository
from src.database.repositories.job_repository import JobRepository
from src.documents.errors import DocumentationUploadSupersededError
from src.documents.processing.persistence import publish_uploaded_documentation
from src.documents.relevance import (
    build_chunk_ref_remap,
    collect_relevance_chunk_ids,
    remap_reused_output_relevance,
)
from src.documents.selection import DocumentationSelection, build_selection_chunk_remap
from src.jobs import lifecycle
from src.jobs.errors import JobClaimLostError
from src.jobs.result_envelope import missing_session_companion_outputs
from src.shared.clock import utc_now
from src.shared.enums import JobStage
from src.shared.job_types import CacheReuse, CacheWindow, JobCachePolicy, JobType
from src.shared.normalize import DOCUMENTATION_SELECTION_INPUT_KEY

logger = logging.getLogger(__name__)

RunNormalWorker = Callable[[], Awaitable[Dict[str, Any]]]


class _CacheReuseUnavailable(RuntimeError):
    pass


def reuse_window(window: CacheWindow) -> timedelta:
    """Return the configured maximum age of a reusable source job."""
    match window:
        case CacheWindow.DIGESTER_INPUT:
            return config.digester.digester_input_check_interval
        case CacheWindow.DISCOVERY_INPUT:
            return config.search.discovery_input_check_interval


async def reuse_or_run(
    *,
    job_type: JobType,
    cache_policy: JobCachePolicy,
    job_id: UUID,
    session_id: UUID,
    input_payload: Dict[str, Any],
    run_normal_worker: RunNormalWorker,
    required_companion_keys: Sequence[str] = (),
) -> Dict[str, Any]:
    """Return a cached/reused result for the job, or the freshly computed worker result.

    The caller decides whether caching applies at all (a cacheable type and no
    ``skipCache``). When a suitable previous job is found, its output is reused as the
    policy says; otherwise ``run_normal_worker`` is awaited and its result returned.
    """
    logger.info("[Jobs:Cache] %s: skipCache is false, checking for previous job output", job_type)

    created_at_limit = utc_now() - reuse_window(cache_policy.window)
    async with async_session_maker() as db:
        latest_job = await JobRepository(db).get_job_by_input(
            job_type,
            input_payload,
            created_at_limit,
            requesting_session_id=session_id,
        )

    if not (latest_job and latest_job.result):
        logger.info(
            "[Jobs:Cache] %s: No previous finished job found with same input since %s",
            job_type,
            created_at_limit.isoformat(),
        )
        return await run_normal_worker()

    missing_companions = missing_session_companion_outputs(latest_job.result, required_companion_keys)
    if missing_companions:
        logger.info(
            "[Jobs:Cache] %s: Cached result lacks required companion output(s) %s; running the worker",
            job_type,
            ", ".join(missing_companions),
        )
        return await run_normal_worker()

    cached_result: Dict[str, Any] = latest_job.result
    try:
        await lifecycle.update_job_progress(
            job_id,
            stage=JobStage.processing,
            message=f"Reused output from job {latest_job.job_id}",
        )
        logger.info(
            "[Jobs:Cache] %s: Reusing output from source job %s created at %s",
            job_type,
            latest_job.job_id,
            latest_job.created_at.isoformat(),
        )
        match cache_policy.reuse:
            case CacheReuse.UPLOAD_PUBLICATION:
                return await _republish_uploaded_documentation(
                    job_id, session_id, input_payload, latest_job, cached_result
                )
            case CacheReuse.SESSION_DOCUMENTATION_RELEVANCE:
                return await _remap_by_session_documentation(
                    job_type, session_id, input_payload, latest_job, cached_result
                )
            case CacheReuse.STORED_SELECTION_RELEVANCE:
                return await _remap_by_stored_selection(input_payload, latest_job, cached_result)
            case CacheReuse.RESULT_WITH_ERRORS:
                for message in latest_job.errors or []:
                    await lifecycle.append_job_error(job_id, message)
                return copy.deepcopy(cached_result)
            case CacheReuse.RESULT:
                return copy.deepcopy(cached_result)

    except _CacheReuseUnavailable as exc:
        logger.warning(
            "[Jobs:Cache] %s: Source job %s cannot be reused (%s), running fresh worker",
            job_type,
            latest_job.job_id,
            exc,
        )
        return await run_normal_worker()
    except (JobClaimLostError, DocumentationUploadSupersededError):
        raise
    except Exception:
        logger.exception(
            "[Jobs:Cache] %s: Unexpected failure while reusing output from source job %s",
            job_type,
            latest_job.job_id,
        )
        raise


async def _republish_uploaded_documentation(
    job_id: UUID,
    session_id: UUID,
    input_payload: Dict[str, Any],
    latest_job: Job,
    cached_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Publish the source upload's processed chunks as this upload's document."""
    # Release the read connection before publication acquires its own
    # transaction, so concurrent cache hits cannot exhaust the pool.
    async with async_session_maker() as db:
        latest_job_doc_items = await DocumentationRepository(db).get_documentation_items_by_session_and_job(
            latest_job.session_id, latest_job.job_id
        )
    if not latest_job_doc_items:
        raise _CacheReuseUnavailable("the source job has no documentation items associated")

    await lifecycle.update_job_progress(
        job_id,
        stage=JobStage.processing_chunks,
        message=f"Reusing {len(latest_job_doc_items)} processed documentation chunks from job {latest_job.job_id}",
        total_processing=len(latest_job_doc_items),
        processing_completed=0,
    )
    reused_doc_id = UUID(input_payload["doc_id"]) if input_payload.get("doc_id") else uuid4()
    filename = input_payload.get("filename", "unknown")
    await publish_uploaded_documentation(
        session_id=session_id,
        doc_id=reused_doc_id,
        job_id=job_id,
        filename=filename,
        chunks=[
            {
                "content": item["content"],
                "summary": item["summary"],
                "metadata": {**item["metadata"], "filename": filename},
            }
            for item in latest_job_doc_items
        ],
    )
    await lifecycle.update_job_progress(job_id, processing_completed=len(latest_job_doc_items))
    reused_output = copy.deepcopy(cached_result)
    reused_output.update(doc_id=str(reused_doc_id), filename=filename)
    return reused_output


async def _remap_by_session_documentation(
    job_type: JobType,
    session_id: UUID,
    input_payload: Dict[str, Any],
    latest_job: Job,
    cached_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Remap relevance from the source session's documentation to this job's documentation input."""
    current_doc_items: List[Dict[str, Any]] = input_payload.get("documentationItems", [])
    async with async_session_maker() as db:
        doc_repo = DocumentationRepository(db)
        previous_doc_items = await doc_repo.get_documentation_items_by_session(latest_job.session_id)
        if not current_doc_items:
            current_doc_items = await doc_repo.get_documentation_items_by_session(session_id)

    chunk_ref_remap = build_chunk_ref_remap(
        previous_doc_items=previous_doc_items,
        current_doc_items=current_doc_items,
    )
    if not chunk_ref_remap:
        logger.warning(
            "[Jobs:Cache] %s: Unable to build documentation chunk remap from source job %s, cached relevance "
            "references may be empty in reused output",
            job_type,
            latest_job.job_id,
        )
    return remap_reused_output_relevance(
        copy.deepcopy(cached_result),
        chunk_ref_remap=chunk_ref_remap,
        top_level_doc_refs_snake_case=True,
    )


async def _remap_by_stored_selection(
    input_payload: Dict[str, Any],
    latest_job: Job,
    cached_result: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Remap relevance between the documentation selections stored in both job inputs.

    Equal fingerprints mean both selections hold interchangeable chunks, so every
    reference of the cached output must map; a reference that does not is never
    published (the worker runs instead). The source session's current documentation
    is irrelevant: the source job's input captured what it read.
    """
    async with async_session_maker() as db:
        previous_input = await JobRepository(db).get_job_input(latest_job.job_id)
    try:
        previous_selection = DocumentationSelection.model_validate(
            (previous_input or {}).get(DOCUMENTATION_SELECTION_INPUT_KEY)
        )
        current_selection = DocumentationSelection.model_validate(input_payload.get(DOCUMENTATION_SELECTION_INPUT_KEY))
    except ValidationError as exc:
        raise _CacheReuseUnavailable(
            f"a stored documentation selection is invalid: {exc.error_count()} error(s)"
        ) from exc

    chunk_ref_remap = build_selection_chunk_remap(previous_selection, current_selection)
    unmapped = collect_relevance_chunk_ids(cached_result) - chunk_ref_remap.keys()
    if unmapped:
        raise _CacheReuseUnavailable(
            f"{len(unmapped)} cached relevance reference(s) are outside the source job's stored selection"
        )
    return remap_reused_output_relevance(
        copy.deepcopy(cached_result),
        chunk_ref_remap=chunk_ref_remap,
        top_level_doc_refs_snake_case=True,
    )
