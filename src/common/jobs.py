# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import copy
import logging
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union
from uuid import UUID

from src.common.database.config import async_session_maker
from src.common.database.repositories.documentation_repository import DocumentationRepository
from src.common.database.repositories.job_repository import JobRepository
from src.common.database.repositories.relevant_chunk_repository import RelevantChunkRepository
from src.common.database.repositories.session_repository import SessionRepository
from src.common.enums import JobStage
from src.common.utils.normalize import normalize_input
from src.common.utils.relevance import (
    build_chunk_ref_remap as _build_chunk_ref_remap,
)
from src.common.utils.relevance import (
    build_chunk_to_doc_map as _build_chunk_to_doc_map,
)
from src.common.utils.relevance import (
    extract_relevant_rows_for_storage as _extract_relevant_rows_for_storage,
)
from src.common.utils.relevance import (
    remap_reused_output_relevance as _remap_reused_output_relevance,
)
from src.common.utils.relevance import (
    strip_relevance_from_session_payload as _strip_relevance_from_session_payload,
)
from src.common.utils.relevance import (
    unwrap_result_payload as _unwrap_result_payload,
)
from src.config import config

logger = logging.getLogger(__name__)

_job_futures: Dict[UUID, asyncio.Future] = {}
_background_tasks: set[asyncio.Task] = set()


def _spawn_background_task(coro: Awaitable[Any]) -> asyncio.Task:
    task = asyncio.ensure_future(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def update_job_progress(
    job_id: UUID,
    *,
    stage: Optional[Union[str, JobStage]] = None,
    message: Optional[str] = None,
    total_processing: Optional[int] = None,
    processing_completed: Optional[int] = None,
) -> None:
    """Update progress information for a running job."""
    try:
        async with async_session_maker() as db:
            repo = JobRepository(db)
            await repo.update_job_progress(
                job_id,
                stage=stage,
                message=message,
                total_processing=total_processing,
                processing_completed=processing_completed,
            )
            await db.commit()
    except Exception as e:
        logger.debug("Job progress update failed", exc_info=e)


async def increment_processed_documents(job_id: UUID, delta: int = 1) -> None:
    async with async_session_maker() as db:
        repo = JobRepository(db)
        await repo.increment_processed_documents(job_id, delta)
        await db.commit()


async def create_job(input_payload: Dict[str, Any], job_type: str, session_id: UUID) -> UUID:
    """Create a queued job and return job_id."""
    try:
        async with async_session_maker() as db:
            repo = JobRepository(db)
            job_id = await repo.create_job(input_payload, job_type, session_id)
            await db.commit()
            return job_id
    except Exception as e:
        logger.error("Create job failed.", exc_info=e)
        raise


async def set_running(job_id: UUID) -> Dict[str, Any]:
    """Transition a queued job to running state and return the updated job record."""
    try:
        async with async_session_maker() as db:
            repo = JobRepository(db)
            data = await repo.set_running(job_id)
            await db.commit()
            return data
    except Exception as e:
        logger.debug("Set job running failed.", exc_info=e)
        return {}


async def set_finished(job_id: UUID, result: Dict[str, Any]) -> Dict[str, Any]:
    """Transition a running job to finished state, attach `result`, and return the record."""
    try:
        async with async_session_maker() as db:
            repo = JobRepository(db)
            data = await repo.set_finished(job_id, result)
            await db.commit()

        future = _job_futures.pop(job_id, None)
        if future and not future.done():
            future.set_result(None)

        return data
    except Exception as e:
        logger.error("Set job to finished failed.", exc_info=e)
        raise


async def set_failed(job_id: UUID, error: str) -> Dict[str, Any]:
    """Transition a job to failed state with a normalized list of error messages."""
    try:
        async with async_session_maker() as db:
            repo = JobRepository(db)
            data = await repo.set_failed(job_id, error)
            await db.commit()

        future = _job_futures.pop(job_id, None)
        if future and not future.done():
            future.set_result(None)

        return data
    except Exception as e:
        logger.error("Set job to failed failed.", exc_info=e)
        raise


async def _append_job_error_now(job_id: UUID, message: str) -> None:
    async with async_session_maker() as db:
        repo = JobRepository(db)
        await repo.append_job_error(job_id, message)
        await db.commit()


async def get_job_status(job_id: UUID | None) -> Dict[str, Any]:
    """Return a public job status dict."""
    if job_id is None:
        return {"jobId": None, "status": "not_found"}
    try:
        async with async_session_maker() as db:
            repo = JobRepository(db)
            return await repo.get_job_status(job_id)
    except Exception as e:
        logger.debug("Get a job failed.", exc_info=e)
        return {"jobId": str(job_id), "status": "not_found"}


async def schedule_coroutine_job(
    *,
    job_type: str,
    input_payload: Dict[str, Any],
    dynamic_input_enabled: bool = False,
    dynamic_input_provider: Optional[Callable[..., Awaitable[Any]]] = None,
    worker: Callable[..., Awaitable[Any]],
    worker_args: Optional[Tuple[Any, ...]] = None,
    worker_kwargs: Optional[Dict[str, Any]] = None,
    initial_stage: Optional[Union[str, JobStage]] = None,
    initial_message: Optional[str] = None,
    session_id: UUID,
    session_result_key: Optional[str] = None,
    await_documentation: bool = False,
    await_documentation_timeout: Optional[float] = None,
) -> UUID:
    """
    Create a job record and schedule `worker` coroutine to process it in background.
    The worker must accept the job_id as the last positional argument or via kwarg `job_id` if desired.

    If session_result_key is provided, the result will be automatically
    stored in the session under the given key when the job completes.

    :param session_id: Required session ID for the job
    """

    # Create job in database
    job_id = await create_job(input_payload, job_type, session_id)

    if initial_stage or initial_message:
        await update_job_progress(job_id, stage=initial_stage, message=initial_message)

    future = asyncio.get_event_loop().create_future()
    _job_futures[job_id] = future

    async def _runner() -> None:
        try:
            if await_documentation:
                await update_job_progress(
                    job_id, stage="queue", message="Waiting for documentation processing to complete."
                )
                async with async_session_maker() as db:
                    repo_doc = JobRepository(db)
                    not_finished_jobs_ids = await repo_doc.get_not_finished_documentation_jobs_ids(session_id)
                    if not_finished_jobs_ids:
                        futures = [_job_futures[jid] for jid in not_finished_jobs_ids if jid in _job_futures]
                        if futures:
                            try:
                                await asyncio.wait_for(asyncio.gather(*futures), timeout=await_documentation_timeout)
                            except asyncio.TimeoutError:
                                logger.warning(f"Job {job_id} timed out waiting for documentation jobs to complete")

            dynamic_input = {}
            if dynamic_input_enabled and dynamic_input_provider:
                async with async_session_maker() as db:
                    dynamic_input = await dynamic_input_provider(session_id=session_id, db=db)
                    # Update Job input in Jobs table
                    input_payload.update(dynamic_input.get("jobInput", {}))
                    repo_job = JobRepository(db)
                    await repo_job.update_job_input(job_id, input_payload)
                    # Update operation input in Sessions table
                    repo_session = SessionRepository(db)
                    await repo_session.update_session(session_id, dynamic_input.get("sessionInput", {}))
                    await db.commit()

            await set_running(job_id)
            args = tuple(worker_args or ())

            if dynamic_input_enabled and dynamic_input_provider:
                args += dynamic_input.get("args", ())

            kwargs = dict(worker_kwargs or {})

            # Prefer explicit kwarg if caller wants to pass it
            if "job_id" in worker.__code__.co_varnames:  # type: ignore[attr-defined]
                kwargs.setdefault("job_id", job_id)

            result: Any = None
            result_dict: Dict[str, Any] = {}

            async def run_normal_worker() -> None:
                nonlocal result
                nonlocal result_dict
                result = await worker(*args, **kwargs)
                # Auto-serialize result
                if hasattr(result, "model_dump"):
                    result_dict = result.model_dump(by_alias=True, mode="json")  # type: ignore[attr-defined]
                elif isinstance(result, dict):
                    result_dict = result
                else:
                    result_dict = {"value": repr(result)}

            # TODO: implement caching also for codegen, move scraper implementation here
            if "scrape" not in job_type and not input_payload.get("skipCache", False):
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
                    if latest_job and latest_job.result:
                        try:
                            await update_job_progress(
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
                                latest_job_doc_items: List[
                                    Dict[str, Any]
                                ] = await doc_repo.get_documentation_items_by_session_and_job(
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
                                    await run_normal_worker()
                                else:
                                    await update_job_progress(
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
                                            doc_id=UUID(input_payload.get("doc_id"))
                                            if input_payload.get("doc_id")
                                            else None,
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
                                    await update_job_progress(
                                        job_id,
                                        processing_completed=len(latest_job_doc_items),
                                    )
                                    result_dict = reused_output
                            elif job_type.startswith("digester.") or "relevantDocumentations" in reused_output:
                                previous_doc_items = await doc_repo.get_documentation_items_by_session(
                                    latest_job.session_id
                                )
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

                                result_dict = _remap_reused_output_relevance(
                                    reused_output,
                                    chunk_ref_remap=chunk_ref_remap,
                                    top_level_doc_refs_snake_case=True,
                                )
                            elif "discovery" in job_type or "codegen" in job_type:
                                result_dict = copy.deepcopy(latest_job.result)
                            else:
                                logger.warning(
                                    "[%s] Job %s: Previous job %s has no relevant chunks in result, cannot reuse chunks for current job %s",
                                    job_type,
                                    str(job_id),
                                    str(latest_job.job_id),
                                    str(job_id),
                                )
                                await run_normal_worker()

                        except Exception as exc:
                            logger.warning(
                                "[%s] Job %s: Previous job %s has invalid result payload (%s), running fresh discovery",
                                job_type,
                                str(job_id),
                                str(latest_job.job_id),
                                str(exc),
                            )
                            await run_normal_worker()
                    else:
                        logger.info(
                            "[%s] Job %s: No previous finished job found with same input since %s",
                            job_type,
                            str(job_id),
                            datetime.isoformat(created_at_limits),
                        )
                        await run_normal_worker()
            else:
                await run_normal_worker()

            # Store result in session if requested (before saving to job)
            if session_result_key:
                try:
                    async with async_session_maker() as db:
                        repo = SessionRepository(db)
                        relevant_repo = RelevantChunkRepository(db)

                        if isinstance(result_dict, dict):
                            session_payload: Any
                            if isinstance(result_dict.get("result"), dict):
                                session_payload = _unwrap_result_payload(result_dict)
                            else:
                                session_payload = result_dict

                            session_payload = _strip_relevance_from_session_payload(
                                session_payload,
                                result_key=session_result_key,
                            )
                            await repo.update_session(session_id, {session_result_key: session_payload})

                            chunk_to_doc = _build_chunk_to_doc_map(input_payload.get("documentationItems"))
                            if not chunk_to_doc:
                                doc_repo = DocumentationRepository(db)
                                chunk_to_doc = _build_chunk_to_doc_map(
                                    await doc_repo.get_documentation_items_by_session(session_id)
                                )

                            relevant_rows = _extract_relevant_rows_for_storage(
                                result_dict,
                                result_key=session_result_key,
                                chunk_to_doc=chunk_to_doc,
                            )
                            await relevant_repo.replace_relevant_chunks_for_result(
                                session_id=session_id,
                                result_key=session_result_key,
                                chunks=relevant_rows,
                            )
                        else:
                            await repo.update_session(session_id, {session_result_key: result_dict})

                        await db.commit()
                except Exception as e:
                    error_msg = f"Session persistence failed for job {job_id} in session {session_id}: {e}"
                    logger.error(error_msg, exc_info=e)
                    try:
                        await _append_job_error_now(job_id, error_msg)
                    except Exception:
                        logger.error("Failed to record session persistence error for job %s", job_id, exc_info=True)

            # Prepare job result (exclude large chunks array, keep only metadata)
            job_result_dict = result_dict.copy() if isinstance(result_dict, dict) else result_dict
            if isinstance(job_result_dict, dict) and "chunks" in job_result_dict:
                # Remove the large chunks array from job result, keep metadata
                del job_result_dict["chunks"]
                # metadata already contains summary info about chunks

            await set_finished(job_id, result=job_result_dict)
        except asyncio.CancelledError as cancel_exc:  # graceful cancellation (e.g., shutdown)
            try:
                await set_failed(job_id, error=f"Job cancelled/interrupted: {cancel_exc}")
            except Exception:
                pass
            raise
        except Exception as exc:
            await set_failed(job_id, error=str(exc))

    _spawn_background_task(_runner())
    return job_id


def append_job_error(job_id: UUID, message: str) -> None:
    """
    Append a non-fatal error message to the job record without changing its status.
    Used to surface partial/chunk errors while allowing the job to finish successfully.
    """

    async def _append() -> None:
        try:
            await _append_job_error_now(job_id, message)
        except Exception as e:
            logger.debug(f"Append job error failed for {job_id}", exc_info=e)

    try:
        _spawn_background_task(_append())
    except RuntimeError:
        # No running loop - try to get or create one
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_append())
            else:
                loop.run_until_complete(_append())
        except Exception as ex:
            logger.debug(f"Append job error failed - no event loop: {ex}")


async def recover_stale_running_jobs(note: Optional[str] = None) -> int:
    """
    Move all jobs left in 'running' to 'failed'.
    This is intended to be called on service startup to recover from crashes or hard stops (e.g., CTRL+C).

    :param note: Optional message to include in the error list.
    :return: number of recovered jobs.
    """
    try:
        async with async_session_maker() as db:
            repo = JobRepository(db)
            count = await repo.recover_stale_running_jobs(note)
            await db.commit()
            return count
    except Exception as e:
        logger.error(f"Failed to recover stale running jobs: {e}")
        return 0
