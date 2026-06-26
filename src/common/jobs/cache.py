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
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List
from uuid import UUID

from src.common.database.config import async_session_maker
from src.common.database.repositories.documentation_repository import DocumentationRepository
from src.common.database.repositories.job_repository import JobRepository
from src.common.enums import JobStage
from src.common.jobs import lifecycle
from src.common.utils.normalize import normalize_input
from src.common.utils.relevance import (
    build_chunk_ref_remap as _build_chunk_ref_remap,
)
from src.common.utils.relevance import (
    remap_reused_output_relevance as _remap_reused_output_relevance,
)
from src.config import config

logger = logging.getLogger(__name__)

RunNormalWorker = Callable[[], Awaitable[Dict[str, Any]]]


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
        "[%s] Job %s (session %s): skipCache is false, checking for previous job output",
        job_type,
        str(job_id),
        str(session_id),
    )

    async with async_session_maker() as db:
        job_repo = JobRepository(db)
        doc_repo = DocumentationRepository(db)
        created_at_limits = (
            datetime.now() - config.digester.digester_input_check_interval
            if "digester" in job_type
            else datetime.now() - config.search.discovery_input_check_interval
        )
        normalized_input = normalize_input(input_payload)
        latest_job = await job_repo.get_job_by_input(
            job_type,
            normalized_input,
            created_at_limits,
        )
        if not (latest_job and latest_job.result):
            logger.info(
                "[%s] Job %s: No previous finished job found with same input since %s",
                job_type,
                str(job_id),
                datetime.isoformat(created_at_limits),
            )
            return await run_normal_worker()

        try:
            await lifecycle.update_job_progress(
                job_id,
                stage=JobStage.processing,
                message=f"Reused output from job {latest_job.job_id}",
            )
            logger.info(
                "[%s] Job %s: Reusing output from job %s created at %s",
                job_type,
                str(job_id),
                str(latest_job.job_id),
                datetime.isoformat(latest_job.created_at),
            )
            reused_output: Dict[str, Any] = copy.deepcopy(latest_job.result)
            current_doc_items: List[Dict[str, Any]] = input_payload.get("documentationItems", [])

            if job_type == "documentation.processUpload":
                previous_session_id: UUID = latest_job.session_id
                latest_job_doc_items: List[Dict[str, Any]] = await doc_repo.get_documentation_items_by_session_and_job(
                    previous_session_id, latest_job.job_id
                )
                if not latest_job_doc_items:
                    logger.warning(
                        "[%s] Job %s: Previous job %s has no documentation items associated, cannot reuse processed documentation for current job %s",
                        job_type,
                        str(job_id),
                        str(latest_job.job_id),
                        str(job_id),
                    )
                    return await run_normal_worker()

                await lifecycle.update_job_progress(
                    job_id,
                    stage=JobStage.processing_chunks,
                    message=(
                        f"Reusing {len(latest_job_doc_items)} processed documentation chunks "
                        f"from job {latest_job.job_id}"
                    ),
                    total_processing=len(latest_job_doc_items),
                    processing_completed=0,
                )
                for item in latest_job_doc_items:
                    await doc_repo.create_documentation_item(
                        session_id=session_id,
                        source="upload",
                        content=item["content"],
                        original_job_id=job_id,
                        doc_id=UUID(input_payload.get("doc_id")) if input_payload.get("doc_id") else None,
                        url=f"upload://{input_payload.get('filename', 'unknown')}",
                        summary=item["summary"],
                        metadata={
                            "filename": input_payload.get("filename", "unknown"),
                            "chunk_number": item["metadata"].get("chunk_number"),
                            "token_count": item["metadata"].get("token_count"),
                            "num_endpoints": item["metadata"].get("num_endpoints"),
                            "tags": item["metadata"].get("tags"),
                            "category": item["metadata"].get("category"),
                            "content_type": item["metadata"].get("content_type"),
                            "character_count": item["metadata"].get("character_count"),
                        },
                    )
                await db.commit()
                await lifecycle.update_job_progress(
                    job_id,
                    processing_completed=len(latest_job_doc_items),
                )
                return reused_output

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
                        "[%s] Job %s: Unable to build documentation chunk remap from job %s, cached relevance references may be empty in reused output",
                        job_type,
                        str(job_id),
                        str(latest_job.job_id),
                    )

                return _remap_reused_output_relevance(
                    reused_output,
                    chunk_ref_remap=chunk_ref_remap,
                    top_level_doc_refs_snake_case=True,
                )

            if "discovery" in job_type or "codegen" in job_type:
                return copy.deepcopy(latest_job.result)

            logger.warning(
                "[%s] Job %s: Previous job %s has no relevant chunks in result, cannot reuse chunks for current job %s",
                job_type,
                str(job_id),
                str(latest_job.job_id),
                str(job_id),
            )
            return await run_normal_worker()

        except Exception as exc:
            logger.warning(
                "[%s] Job %s: Previous job %s has invalid result payload (%s), running fresh discovery",
                job_type,
                str(job_id),
                str(latest_job.job_id),
                str(exc),
            )
            return await run_normal_worker()
