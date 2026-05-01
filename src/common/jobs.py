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
from src.common.session.schema import DocumentationItem
from src.common.utils.normalize import normalize_input, normalize_relevant_chunks_for_session
from src.config import config

logger = logging.getLogger(__name__)

_job_futures: Dict[UUID, asyncio.Future] = {}


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
                    session_repo = SessionRepository(db)
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
                                docs_to_reuse: List[Dict[str, Any]] = []
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
                                    for item in latest_job_doc_items:
                                        new_doc = copy.deepcopy(item)
                                        new_doc["doc_id"] = input_payload.get("doc_id")
                                        chunk_id = await doc_repo.create_documentation_item(
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
                                                "length": item["metadata"].get("length"),
                                                "num_endpoints": item["metadata"].get("num_endpoints"),
                                                "tags": item["metadata"].get("tags"),
                                                "category": item["metadata"].get("category"),
                                                "llm_tags": item["metadata"].get("llm_tags"),
                                                "llm_category": item["metadata"].get("llm_category"),
                                            },
                                        )
                                        new_doc["chunk_id"] = chunk_id
                                        new_doc["scrape_job_ids"] = [str(job_id)]

                                        docs_to_reuse.append(
                                            DocumentationItem(**new_doc).model_dump(by_alias=True, mode="json")
                                        )  # type: ignore[attr-defined]
                                    # TODO: maybe we can also solve duplicity
                                    existing_docs = (
                                        await session_repo.get_session_data(session_id, "documentationItems") or []
                                    )
                                    for item in docs_to_reuse:
                                        existing_docs.append(item)
                                    await session_repo.update_session(session_id, {"documentationItems": existing_docs})
                                    await db.commit()
                                    result_dict = reused_output
                            elif job_type == "digester.getObjectClass":
                                rel_repo = RelevantChunkRepository(db)
                                rel_chunks: List[Dict[str, Any]] = []
                                new_relevant_chunks: List[Dict[str, Any]] = []
                                seen_relevant_chunks: set[tuple[str, str]] = set()
                                object_classes = reused_output.get("result", {}).get("objectClasses", [])
                                if not object_classes:
                                    logger.warning(
                                        "[%s] Job %s: Previous job %s has no object classes in result, cannot reuse chunks for current job %s",
                                        job_type,
                                        str(job_id),
                                        str(latest_job.job_id),
                                        str(job_id),
                                    )
                                    await run_normal_worker()
                                else:
                                    for objClass in reused_output.get("result", {}).get("objectClasses", []):
                                        class_name = objClass.get("name")
                                        relevant_chunks_obj_class: List[Dict[str, Any]] = objClass.get(
                                            "relevantDocumentations", []
                                        )
                                        remapped_obj_class_chunks: List[Dict[str, Any]] = []

                                        for rel_chunk in relevant_chunks_obj_class:
                                            original_chunk_id = rel_chunk.get("chunk_id") or rel_chunk.get("chunkId")
                                            if not original_chunk_id:
                                                logger.warning(
                                                    "[%s] Job %s: Corrupted relevant chunk with no chunk_id for object class %s when trying to reuse output from job %s, skipping this relevant chunk",
                                                    job_type,
                                                    str(job_id),
                                                    class_name,
                                                    str(latest_job.job_id),
                                                )
                                                continue

                                            orig_doc = await doc_repo.get_documentation_item(
                                                UUID(str(original_chunk_id))
                                            )
                                            content = orig_doc["content"] if orig_doc else ""
                                            url = orig_doc["url"] if orig_doc else ""
                                            current_doc = [
                                                item
                                                for item in current_doc_items
                                                if item.get("url") == url and item.get("content") == content
                                            ]
                                            if not current_doc or len(current_doc) == 0 or len(current_doc) > 1:
                                                logger.warning(
                                                    "[%s] Job %s: Corrupted documentation items for object class %s when trying to reuse output from job %s, cannot find unique match in current input, skipping relevant chunks for this object class",
                                                    job_type,
                                                    str(job_id),
                                                    class_name,
                                                    str(latest_job.job_id),
                                                )
                                                continue

                                            current_doc_item = current_doc[0]
                                            current_chunk_id = current_doc_item.get("chunkId")
                                            current_doc_id = current_doc_item.get("docId")
                                            if not current_chunk_id or not current_doc_id:
                                                logger.warning(
                                                    "[%s] Job %s: Corrupted documentation item for object class %s when trying to reuse output from job %s, missing chunkId/docId in current input, skipping relevant chunks for this object class",
                                                    job_type,
                                                    str(job_id),
                                                    class_name,
                                                    str(latest_job.job_id),
                                                )
                                                continue

                                            mapped_chunk_for_object_class = {
                                                "chunkId": str(current_chunk_id),
                                                "docId": str(current_doc_id),
                                            }
                                            remapped_obj_class_chunks.append(mapped_chunk_for_object_class)

                                            mapped_chunk_for_output = {
                                                "chunk_id": str(current_chunk_id),
                                                "doc_id": str(current_doc_id),
                                            }

                                            dedupe_key = (
                                                mapped_chunk_for_output["doc_id"],
                                                mapped_chunk_for_output["chunk_id"],
                                            )
                                            if dedupe_key not in seen_relevant_chunks:
                                                seen_relevant_chunks.add(dedupe_key)
                                                new_relevant_chunks.append(mapped_chunk_for_output)

                                            if class_name:
                                                rel_chunks.append(
                                                    {
                                                        "entity_type": class_name,
                                                        "doc_id": UUID(str(current_chunk_id)),
                                                    }
                                                )

                                        objClass["relevantDocumentations"] = remapped_obj_class_chunks

                                    reused_output["relevantDocumentations"] = new_relevant_chunks

                                    await rel_repo.bulk_add_relevant_chunks(
                                        session_id,
                                        rel_chunks,
                                    )
                                    await db.commit()
                                    result_dict = reused_output

                            elif "relevantDocumentations" in reused_output:
                                original_relevant_chunks: List[Dict[str, Any]] = reused_output.pop(
                                    "relevantDocumentations"
                                )

                                new_relevant_chunks = []
                                for rel_chunk in original_relevant_chunks:
                                    original_chunk_id = rel_chunk.get("chunk_id") or rel_chunk.get("chunkId")
                                    if not original_chunk_id:
                                        logger.warning(
                                            "[%s] Job %s: Corrupted relevant chunk with no chunk_id for key %s when trying to reuse output from job %s, skipping this relevant chunk",
                                            job_type,
                                            str(job_id),
                                            rel_chunk,
                                            str(latest_job.job_id),
                                        )
                                        continue

                                    orig_doc = await doc_repo.get_documentation_item(UUID(str(original_chunk_id)))
                                    content = orig_doc["content"] if orig_doc else ""
                                    url = orig_doc["url"] if orig_doc else ""
                                    current_doc = [
                                        item
                                        for item in current_doc_items
                                        if item.get("url") == url and item.get("content") == content
                                    ]
                                    if not current_doc or len(current_doc) == 0 or len(current_doc) > 1:
                                        logger.warning(
                                            "[%s] Job %s: Corrupted documentation items for key %s when trying to reuse output from job %s, cannot find unique match in current input, skipping relevant chunks for this item",
                                            job_type,
                                            str(job_id),
                                            rel_chunk,
                                            str(latest_job.job_id),
                                        )
                                        continue

                                    current_doc_item = current_doc[0]
                                    current_chunk_id = current_doc_item.get("chunkId")
                                    current_doc_id = current_doc_item.get("docId")
                                    if not current_chunk_id or not current_doc_id:
                                        logger.warning(
                                            "[%s] Job %s: Corrupted documentation item for key %s when trying to reuse output from job %s, missing chunkId/docId in current input, skipping relevant chunks for this item",
                                            job_type,
                                            str(job_id),
                                            rel_chunk,
                                            str(latest_job.job_id),
                                        )
                                        continue

                                    new_relevant_chunks.append(
                                        {
                                            "doc_id": str(current_doc_id),
                                            "chunk_id": str(current_chunk_id),
                                        }
                                    )
                                logger.info(
                                    "[%s] Job %s: Reusing output from job %s with %s relevant chunks updated to match current input, first relevant chunk_id %s if exists",
                                    job_type,
                                    str(job_id),
                                    str(latest_job.job_id),
                                    len(new_relevant_chunks),
                                    new_relevant_chunks[0]["chunk_id"] if new_relevant_chunks else None,
                                )
                                reused_output["relevantDocumentations"] = new_relevant_chunks
                                result_dict = reused_output
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

                        # Handle new format with chunks metadata
                        if (
                            isinstance(result_dict, dict)
                            and "result" in result_dict
                            and ("relevant_chunk_indices" in result_dict or "relevantDocumentations" in result_dict)
                        ):
                            # New format: store result and relevant chunks separately
                            actual_result = result_dict["result"]
                            # Support both old format (relevant_chunk_indices) and new format (relevant_chunks)
                            relevant_chunks = result_dict.get("relevantDocumentations") or result_dict.get(
                                "relevant_chunk_indices", []
                            )
                            normalized_relevant_chunks = normalize_relevant_chunks_for_session(relevant_chunks)

                            session_updates = {session_result_key: actual_result}

                            # Store relevant chunks for this specific extraction
                            if normalized_relevant_chunks:
                                # Get existing relevant_chunks dict or create new one
                                existing_relevant = (
                                    await repo.get_session_data(session_id, "relevantDocumentations") or {}
                                )
                                if isinstance(existing_relevant, dict):
                                    existing_relevant = {
                                        k: normalize_relevant_chunks_for_session(v)
                                        for k, v in existing_relevant.items()
                                    }
                                else:
                                    existing_relevant = {}
                                existing_relevant[session_result_key] = normalized_relevant_chunks
                                session_updates["relevantDocumentations"] = existing_relevant

                            await repo.update_session(session_id, session_updates)
                        else:
                            await repo.update_session(session_id, {session_result_key: result_dict})
                        await db.commit()
                except Exception as e:
                    logger.error(f"Failed to update session {session_id} after job {job_id}: {e}")
                    # Don't fail the job if session update fails
                    pass

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

    asyncio.create_task(_runner())
    return job_id


def append_job_error(job_id: UUID, message: str) -> None:
    """
    Append a non-fatal error message to the job record without changing its status.
    Used to surface partial/chunk errors while allowing the job to finish successfully.
    """

    async def _append() -> None:
        try:
            async with async_session_maker() as db:
                repo = JobRepository(db)
                await repo.append_job_error(job_id, message)
                await db.commit()
        except Exception as e:
            logger.debug(f"Append job error failed for {job_id}", exc_info=e)

    try:
        asyncio.create_task(_append())
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
