# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from crawl4ai.utils import get_base_domain  # type: ignore

from src.common.chunk_processor.processor import process_all_documentations
from src.common.chunk_processor.schema import SavedDocumentation
from src.common.database.config import async_session_maker
from src.common.database.repositories.documentation_repository import DocumentationRepository
from src.common.database.repositories.job_repository import JobRepository
from src.common.database.repositories.session_repository import SessionRepository
from src.common.enums import JobStage
from src.common.jobs import update_job_progress
from src.common.session.schema import DocumentationItem
from src.config import config
from src.modules.scrape.fucntions import scraper_loop
from src.modules.scrape.schema import ScrapeRequest, ScrapeResult

logger = logging.getLogger(__name__)


async def _run_scrape_async(input: ScrapeRequest, job_id: UUID, session_id: Optional[UUID] = None) -> ScrapeResult:
    if input.use_previous_session_data and session_id:
        logger.info(
            "[Scrape] Job %s (session %s): use_previous_session_data is True, checking for existing documentation items in all sessions for the same input",
            str(job_id),
            str(session_id),
        )
        async with async_session_maker() as db:
            job_repo = JobRepository(db)
            created_at_limits = datetime.now() - config.scrape_and_process.scrape_input_check_interval
            normalized_input = input.model_dump(by_alias=True, exclude={"use_previous_session_data"})
            latest_job = await job_repo.get_job_by_input(
                "scrape.getRelevantDocumentation", normalized_input, created_at_limits
            )
            if latest_job:
                logger.info(
                    "[Scrape] Job %s: Found previous job %s with same input created at %s, reusing its documentation items",
                    job_id,
                    str(latest_job.job_id),
                    datetime.isoformat(latest_job.created_at),
                )
                repo = SessionRepository(db)
                doc_repo = DocumentationRepository(db)
                doc_items = await doc_repo.get_documentation_items_by_session_and_job(
                    latest_job.session_id, latest_job.job_id
                )
                if doc_items:
                    logger.info(
                        "[Scrape] Job %s: Found %s documentation items from previous job %s, saving to current session",
                        job_id,
                        len(doc_items),
                        latest_job.job_id,
                    )
                    # Save these items to the current session
                    existing_docs_loaded: List[Dict[str, Any]] = (
                        await repo.get_session_data(session_id, "documentationItems") or []
                    )
                    existing_docs_urls = {item.get("url") for item in existing_docs_loaded if item.get("url")}
                    new_docs = [item for item in doc_items if item["url"] not in existing_docs_urls]
                    updated_chunks_existing: List[Dict[str, Any]] = []
                    for chunk in new_docs:
                        chunk_id = await doc_repo.create_documentation_item(
                            session_id=session_id,
                            source="scraper",
                            original_job_id=job_id,
                            content=chunk["content"],
                            doc_id=chunk["docId"],
                            url=chunk["url"],
                            summary=chunk["summary"],
                            metadata=chunk["metadata"],
                        )
                        chunk["scrapeJobIds"] = [job_id]
                        chunk["chunkId"] = chunk_id
                        updated_chunks_existing.append(
                            DocumentationItem(**chunk).model_dump(by_alias=True, mode="json")
                        )
                    for chunk in existing_docs_loaded:
                        chunk_id = UUID(chunk.get("chunkId", ""))
                        if chunk_id:
                            update_res = await doc_repo.update_documentation_item(
                                chunk_id=chunk_id, original_job_id=job_id
                            )
                            if not update_res:
                                logger.warning(
                                    "[Scrape] Job %s: Failed to update existing documentation item with ID %s to link to job %s",
                                    job_id,
                                    chunk_id,
                                    job_id,
                                )
                        else:
                            logger.warning(
                                "[Scrape] Job %s: Existing documentation item in session is missing ID, cannot link to job %s",
                                job_id,
                                job_id,
                            )

                    all_docs = existing_docs_loaded + updated_chunks_existing
                    await repo.update_session(session_id, {"documentationItems": all_docs})
                    await db.commit()
                    logger.info(
                        "[Scrape] Job %s: Saved %s documentation items to session (total now: %s)",
                        job_id,
                        len(updated_chunks_existing),
                        len(all_docs),
                    )

                    orig_job_result = latest_job.result
                    return ScrapeResult(
                        finish_reason="reused_previous_session_data",
                        saved_documentations_count=orig_job_result.get("savedDocumentationsCount", 0)
                        if orig_job_result
                        else 0,
                        documentation_chunks_count=len(updated_chunks_existing),
                        saved_documentations=orig_job_result.get("savedDocumentations", {}) if orig_job_result else {},
                    )
                else:
                    logger.warning(
                        "[Scrape] Job %s: No documentation items found from previous job %s, proceeding with fresh scrape",
                        job_id,
                        latest_job.job_id,
                    )
            else:
                logger.info(
                    "[Scrape] Job %s: No previous job found with same input since %s, proceeding with fresh scrape",
                    job_id,
                    datetime.isoformat(datetime.now() - config.scrape_and_process.scrape_input_check_interval),
                )

    logger.info("[Scrape] Starting scrape job %s for session %s", job_id, session_id)
    await update_job_progress(job_id, stage=JobStage.running, message="initializing scraper")

    if not input.starter_links:
        logger.warning("[Scrape] No starter links provided for job %s", job_id)
        await update_job_progress(job_id, stage=JobStage.failed, message="no-starter-links provided")
        return ScrapeResult(
            finish_reason="no-starter-links",
            saved_documentations_count=0,
            documentation_chunks_count=0,
            saved_documentations={},
        )

    # Trusted domains
    trusted_domains = list({get_base_domain(u) for u in input.starter_links})
    logger.info("[Scrape] Trusted domains for job %s: %s", job_id, trusted_domains)

    forbidden_url_parts = config.scrape_and_process.forbidden_url_parts

    # Main scrape loop
    saved_documentations: Dict[str, SavedDocumentation] = {}
    irrelevant_links: List[str] = []
    links = list(input.starter_links)

    existing_documentation_chunks: List[Dict[str, Any]] = []
    existing_documentation_chunks_urls: set[str] = set()
    if session_id:
        async with async_session_maker() as db:
            repo = SessionRepository(db)
            existing_documentation_chunks = await repo.get_session_data(session_id, "documentationItems") or []
        existing_documentation_chunks_urls = {
            str(url).rstrip("/") for item in existing_documentation_chunks for url in [item.get("url")] if url
        }
        logger.info(
            "[Scrape] Job %s: Loaded %s existing documentation entries (%s unique URLs) for incremental processing",
            job_id,
            len(existing_documentation_chunks),
            len(existing_documentation_chunks_urls),
        )

    processing_tasks: List[tuple[SavedDocumentation, asyncio.Task[List[DocumentationItem]]]] = []
    processing_semaphore = asyncio.Semaphore(config.scrape_and_process.max_concurrent)
    scheduled_documentations: List[SavedDocumentation] = []
    scheduled_documentation_urls: set[str] = set()

    async def on_documentation_scraped(documentation: SavedDocumentation) -> None:
        normalized_documentation_url = str(documentation.url).rstrip("/")
        if normalized_documentation_url in existing_documentation_chunks_urls:
            return
        if normalized_documentation_url in scheduled_documentation_urls:
            return
        scheduled_documentation_urls.add(normalized_documentation_url)
        scheduled_documentations.append(documentation)
        processing_tasks.append(
            (
                documentation,
                asyncio.create_task(
                    process_all_documentations(
                        [documentation],
                        app=input.application_name,
                        app_version=input.application_version,
                        source="scraper",
                        semaphore=processing_semaphore,
                        chunk_length=config.scrape_and_process.chunk_length,
                    )
                ),
            )
        )

    max_iters = max(0, config.scrape_and_process.max_scraper_iterations)
    if max_iters <= 0:
        logger.error(
            "[Scrape] Invalid scraper iterations value %s for job %s",
            config.scrape_and_process.max_scraper_iterations,
            job_id,
        )
        await update_job_progress(job_id, stage=JobStage.failed, message="wrong-scraper-iterations-value")
        return ScrapeResult(
            finish_reason="wrong-scraper-iterations-value",
            saved_documentations_count=0,
            documentation_chunks_count=0,
            saved_documentations={},
        )

    logger.info("[Scrape] Starting scraper loop for job %s with max %s iterations", job_id, max_iters)

    finish_reason = "max-iterations-reached"

    try:
        for curr_iter in range(1, max_iters + 1):
            logger.info(
                "[Scrape] Job %s: Starting iteration %s/%s with %s links to scrape",
                job_id,
                curr_iter,
                max_iters,
                len(links),
            )
            await update_job_progress(
                job_id,
                processing_completed=curr_iter - 1,
                total_processing=max_iters,
                stage="scraping",
                message=f"running iteration {curr_iter}/{max_iters}",
            )

            new_links = await scraper_loop(
                links_to_scrape=links,
                app=input.application_name,
                app_version=input.application_version,
                max_iterations_filter_irrelevant=config.scrape_and_process.max_iterations_filter_irrelevant,
                curr_iteration=curr_iter,
                irrelevant_links=irrelevant_links,
                saved_documentations=saved_documentations,
                trusted_domains=trusted_domains,
                forbidden_url_parts=forbidden_url_parts,
                last_iteration=(curr_iter == max_iters),
                on_documentation_scraped=on_documentation_scraped,
            )

            logger.info(
                "[Scrape] Job %s: Iteration %s/%s complete. New links: %s, total saved documentations: %s",
                job_id,
                curr_iter,
                max_iters,
                len(new_links),
                len(saved_documentations),
            )

            # Update progress after iteration completes
            await update_job_progress(
                job_id,
                processing_completed=curr_iter,
                total_processing=max_iters,
                stage="scraping",
                message=f"completed iteration {curr_iter}/{max_iters}",
            )

            if not new_links:
                logger.info(
                    "[Scrape] Job %s: No more links to scrape, finishing early at iteration %s", job_id, curr_iter
                )
                finish_reason = "no-more-links"
                break
            links = new_links
    except Exception:
        for _, task in processing_tasks:
            if not task.done():
                task.cancel()
        if processing_tasks:
            await asyncio.gather(*(task for _, task in processing_tasks), return_exceptions=True)
        raise

    # Finalize chunk processing tasks that have been running in parallel with scraping.
    documentation_chunks: List[DocumentationItem] = []
    if processing_tasks:
        logger.info(
            "[Scrape] Job %s: Awaiting %s chunk-processing tasks for %s scheduled documentations",
            job_id,
            len(processing_tasks),
            len(scheduled_documentations),
        )
        await update_job_progress(job_id, stage=JobStage.processing_chunks, message="finalizing chunk processing")
        processed_batches = await asyncio.gather(*(task for _, task in processing_tasks), return_exceptions=True)
        for (documentation, _), batch in zip(processing_tasks, processed_batches):
            if isinstance(batch, BaseException):
                logger.exception(
                    "[Scrape] Job %s: Chunk processing failed for documentation %s",
                    job_id,
                    documentation.url,
                    exc_info=batch,
                )
                raise RuntimeError(f"Chunk processing failed for documentation {documentation.url}: {batch}") from batch
            documentation_chunks.extend(batch)
    else:
        logger.info("[Scrape] Job %s: No documentations queued for chunk processing", job_id)

    if session_id:
        async with async_session_maker() as db:
            repo = SessionRepository(db)
            doc_repo = DocumentationRepository(db)

            updated_existing_chunks = False
            for chunk in existing_documentation_chunks:
                chunk_id = UUID(chunk.get("chunkId"))
                if chunk_id:
                    update_res = await doc_repo.update_documentation_item(chunk_id=chunk_id, original_job_id=job_id)
                    if update_res:
                        updated_existing_chunks = True
                    else:
                        logger.warning(
                            "[Scrape] Job %s: Failed to update existing documentation item with ID %s to link to job %s",
                            job_id,
                            chunk_id,
                            job_id,
                        )
                else:
                    logger.warning(
                        "[Scrape] Job %s: Existing documentation item in session is missing ID, cannot link to job %s",
                        job_id,
                        job_id,
                    )

            if documentation_chunks:
                logger.info(
                    "[Scrape] Job %s: Converting %s documentation chunks to documentation items",
                    job_id,
                    len(documentation_chunks),
                )

                # Create documentation items in DB and update chunks with DB IDs
                updated_chunks: List[DocumentationItem] = []
                for documentation_chunk in documentation_chunks:
                    chunk_id = await doc_repo.create_documentation_item(
                        session_id=session_id,
                        source="scraper",
                        original_job_id=job_id,
                        content=documentation_chunk.content,
                        doc_id=documentation_chunk.doc_id,
                        url=documentation_chunk.url,
                        summary=documentation_chunk.summary,
                        metadata=documentation_chunk.metadata,
                    )
                    # Update chunk ID to match database
                    documentation_chunk.chunk_id = chunk_id
                    updated_chunks.append(documentation_chunk)

                # Get existing documentation items (if any from uploads)
                existing_docs = await repo.get_session_data(session_id, "documentationItems") or []

                # Append new scraped items with DB IDs (use mode='json' to serialize UUIDs as strings)
                all_docs = existing_docs + [doc.model_dump(by_alias=True, mode="json") for doc in updated_chunks]

                # Save to session
                await repo.update_session(session_id, {"documentationItems": all_docs})
                await db.commit()

                logger.info(
                    "[Scrape] Job %s: Saved %s documentation items to session (total: %s)",
                    job_id,
                    len(documentation_chunks),
                    len(all_docs),
                )
            elif updated_existing_chunks:
                await db.commit()

    result = ScrapeResult(
        finish_reason=finish_reason,
        saved_documentations_count=len(scheduled_documentations),
        documentation_chunks_count=len(documentation_chunks),
        saved_documentations={str(p.url): p.to_dict() for p in scheduled_documentations},
    )

    logger.info(
        "[Scrape] Job %s completed with reason '%s': %s documentations, %s chunks, %s irrelevant links",
        job_id,
        finish_reason,
        len(scheduled_documentations),
        len(documentation_chunks),
        len(irrelevant_links),
    )

    return result


async def fetch_relevant_documentation(
    input: ScrapeRequest,
    session_id: Optional[UUID] = None,
    *,
    job_id: UUID,
) -> ScrapeResult:
    """
    Async entrypoint used by the router. Runs the async scrape workflow directly.
    """
    return await _run_scrape_async(input, job_id=job_id, session_id=session_id)
