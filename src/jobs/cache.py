# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Output reuse (caching) for jobs.

Before running a worker, look for a recent finished job with the same normalized input
and, when possible, reuse its result instead of recomputing it. When the previous output
cannot be reused, fall back to running the worker via the provided ``run_normal_worker``
callback.
"""

import copy
import logging
from typing import Any, Awaitable, Callable, Dict, List
from uuid import UUID, uuid4

from src.config import config
from src.core.db import async_session_maker
from src.database.repositories.documentation_repository import DocumentationRepository
from src.database.repositories.job_repository import JobRepository
from src.documents.errors import DocumentationUploadSupersededError
from src.documents.processing.persistence import publish_uploaded_documentation
from src.documents.relevance import (
    build_chunk_ref_remap as _build_chunk_ref_remap,
)
from src.documents.relevance import (
    remap_reused_output_relevance as _remap_reused_output_relevance,
)
from src.jobs import lifecycle
from src.jobs.errors import JobClaimLostError
from src.shared.clock import utc_now
from src.shared.enums import JobStage

logger = logging.getLogger(__name__)

RunNormalWorker = Callable[[], Awaitable[Dict[str, Any]]]


class _CacheReuseUnavailable(RuntimeError):
    pass


async def reuse_or_run(
    *,
    job_type: str,
    job_id: UUID,
    session_id: UUID,
    input_payload: Dict[str, Any],
    run_normal_worker: RunNormalWorker,
) -> Dict[str, Any]:
    """Return a cached/reused result for the job, or the freshly computed worker result.

    The caller is responsible for deciding whether caching applies (e.g. ``skipCache`` and
    non-cacheable job types). When a suitable previous job is found, its output is reused
    (remapping documentation/relevance references where needed); otherwise
    ``run_normal_worker`` is awaited and its result returned.
    """
    logger.info(
        "[Jobs:Cache] %s: skipCache is false, checking for previous job output",
        job_type,
    )

    created_at_limits = (
        utc_now() - config.digester.digester_input_check_interval
        if "digester" in job_type
        else utc_now() - config.search.discovery_input_check_interval
    )
    async with async_session_maker() as db:
        latest_job = await JobRepository(db).get_job_by_input(
            job_type,
            input_payload,
            created_at_limits,
            requesting_session_id=session_id,
        )

    if not (latest_job and latest_job.result):
        logger.info(
            "[Jobs:Cache] %s: No previous finished job found with same input since %s",
            job_type,
            created_at_limits.isoformat(),
        )
        return await run_normal_worker()

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
        reused_output: Dict[str, Any] = copy.deepcopy(latest_job.result)
        current_doc_items: List[Dict[str, Any]] = input_payload.get("documentationItems", [])

        if job_type == "documentation.processUpload":
            previous_session_id: UUID = latest_job.session_id
            # Release the read connection before publication acquires its own
            # transaction, so concurrent cache hits cannot exhaust the pool.
            async with async_session_maker() as db:
                latest_job_doc_items = await DocumentationRepository(db).get_documentation_items_by_session_and_job(
                    previous_session_id, latest_job.job_id
                )
            if not latest_job_doc_items:
                logger.warning(
                    "[Jobs:Cache] %s: Source job %s has no documentation items associated, cannot reuse processed documentation",
                    job_type,
                    latest_job.job_id,
                )
                raise _CacheReuseUnavailable

            await lifecycle.update_job_progress(
                job_id,
                stage=JobStage.processing_chunks,
                message=(
                    f"Reusing {len(latest_job_doc_items)} processed documentation chunks from job {latest_job.job_id}"
                ),
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
            reused_output.update(doc_id=str(reused_doc_id), filename=filename)
            await lifecycle.update_job_progress(
                job_id,
                processing_completed=len(latest_job_doc_items),
            )
            return reused_output

        async with async_session_maker() as db:
            doc_repo = DocumentationRepository(db)
            if job_type.startswith("digester.") or "relevantDocumentations" in reused_output:
                previous_doc_items = await doc_repo.get_documentation_items_by_session(latest_job.session_id)
                if not current_doc_items:
                    current_doc_items = await doc_repo.get_documentation_items_by_session(session_id)

                chunk_ref_remap = _build_chunk_ref_remap(
                    previous_doc_items=previous_doc_items,
                    current_doc_items=current_doc_items,
                )
                if not chunk_ref_remap:
                    logger.warning(
                        "[Jobs:Cache] %s: Unable to build documentation chunk remap from source job %s, cached relevance references may be empty in reused output",
                        job_type,
                        latest_job.job_id,
                    )

                return _remap_reused_output_relevance(
                    reused_output,
                    chunk_ref_remap=chunk_ref_remap,
                    top_level_doc_refs_snake_case=True,
                )

            if job_type.startswith("codegen."):
                for message in latest_job.errors or []:
                    await lifecycle.append_job_error(job_id, message)
                return reused_output

            if "discovery" in job_type:
                return copy.deepcopy(latest_job.result)

            logger.warning(
                "[Jobs:Cache] %s: Source job %s has no relevant chunks in result, cannot reuse chunks",
                job_type,
                latest_job.job_id,
            )
            raise _CacheReuseUnavailable

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
