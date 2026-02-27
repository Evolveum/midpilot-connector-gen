"""
New scrape service integrating scraperV3 (fucntions.py) to replace the old logic.
Provides a POST job to start scraping and a GET to poll status via the existing job system.
"""

# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from crawl4ai.utils import get_base_domain  # type: ignore

from ...common.chunk_processor.processor import process_all_pages
from ...common.chunk_processor.schema import SavedPage
from ...common.database.config import async_session_maker
from ...common.database.repositories.documentation_repository import DocumentationRepository
from ...common.database.repositories.job_repository import JobRepository
from ...common.database.repositories.session_repository import SessionRepository
from ...common.enums import JobStage
from ...common.jobs import update_job_progress
from ...common.metadata import generate_metadata_from_doc_items
from ...common.session.schema import DocumentationItem
from ...config import config
from .fucntions import scraper_loop
from .schema import ScrapeRequest, ScrapeResult

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
                        doc_id = await doc_repo.create_documentation_item(
                            session_id=session_id,
                            source="scraper",
                            original_job_id=job_id,
                            content=chunk["content"],
                            page_id=chunk["pageId"],
                            url=chunk["url"],
                            summary=chunk["summary"],
                            metadata=chunk["metadata"],
                        )
                        # Store with "uuid" key to match the normal scraper path
                        # (model_dump with by_alias=True uses serialization_alias="uuid")
                        chunk["uuid"] = str(doc_id)
                        chunk.pop("id", None)  # remove legacy "id" key to avoid ambiguity
                        updated_chunks_existing.append(chunk)
                    for chunk in existing_docs_loaded:
                        chunk_id = chunk.get("uuid") or chunk.get("id")
                        if chunk_id:
                            update_res = await doc_repo.update_documentation_item(
                                item_id=UUID(chunk_id), original_job_id=job_id
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
                        saved_pages_count=orig_job_result.get("savedPagesCount", 0) if orig_job_result else 0,
                        page_chunks_count=len(updated_chunks_existing),
                        saved_pages=orig_job_result.get("savedPages", {}) if orig_job_result else {},
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
            saved_pages_count=0,
            page_chunks_count=0,
            saved_pages={},
        )

    # Trusted domains
    trusted_domains = list({get_base_domain(u) for u in input.starter_links})
    logger.info("[Scrape] Trusted domains for job %s: %s", job_id, trusted_domains)

    forbidden_url_parts = config.scrape_and_process.forbidden_url_parts

    # Main scrape loop
    saved_pages: Dict[str, SavedPage] = {}
    irrelevant_links: List[str] = []

    links = list(input.starter_links)

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
            saved_pages_count=0,
            page_chunks_count=0,
            saved_pages={},
        )

    logger.info("[Scrape] Starting scraper loop for job %s with max %s iterations", job_id, max_iters)

    finish_reason = "max-iterations-reached"

    for curr_iter in range(1, max_iters + 1):
        logger.info(
            "[Scrape] Job %s: Starting iteration %s/%s with %s links to scrape",
            job_id,
            curr_iter,
            max_iters,
            len(links),
        )
        # Update progress at start of iteration - shows N-1 completed, iteration N running
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
            max_scraper_iterations=max_iters,
            curr_iteration=curr_iter,
            irrelevant_links=irrelevant_links,
            saved_pages=saved_pages,
            trusted_domains=trusted_domains,
            forbidden_url_parts=forbidden_url_parts,
        )

        logger.info(
            "[Scrape] Job %s: Iteration %s/%s complete. New links: %s, total saved pages: %s",
            job_id,
            curr_iter,
            max_iters,
            len(new_links),
            len(saved_pages),
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
            logger.info("[Scrape] Job %s: No more links to scrape, finishing early at iteration %s", job_id, curr_iter)
            finish_reason = "no-more-links"
            break
        links = new_links

    # Chunk processing
    logger.info("[Scrape] Job %s: Starting chunk processing for %s saved pages", job_id, len(saved_pages))
    await update_job_progress(job_id, stage=JobStage.processing_chunks, message="processing scraped documents")

    pages_list = list(saved_pages.values())

    if session_id:
        async with async_session_maker() as db:
            repo = SessionRepository(db)
            doc_repo = DocumentationRepository(db)

            existing_page_chunks = await repo.get_session_data(session_id, "documentationItems")
            if existing_page_chunks:
                existing_page_chunks_urls = {item.get("url") for item in existing_page_chunks if item.get("url")}
                pages_list = [p for p in pages_list if p.url not in existing_page_chunks_urls]
                logger.info(
                    "[Scrape] Job %s: Skipping %s pages already present in session, processing %s new pages",
                    job_id,
                    len(saved_pages) - len(pages_list),
                    len(pages_list),
                )

            for chunk in existing_page_chunks or []:
                chunk_id = chunk.get("id")
                if chunk_id:
                    update_res = await doc_repo.update_documentation_item(
                        item_id=UUID(chunk_id), original_job_id=job_id
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

            page_chunks: List[DocumentationItem] = (
                await process_all_pages(
                    pages_list,
                    app=input.application_name,
                    app_version=input.application_version,
                    source="scraper",
                )
                if pages_list
                else []
            )

            if page_chunks:
                logger.info(
                    "[Scrape] Job %s: Converting %s page chunks to documentation items", job_id, len(page_chunks)
                )

                # Create documentation items in DB and update chunks with DB IDs
                updated_chunks: List[DocumentationItem] = []
                for page_chunk in page_chunks:
                    doc_id = await doc_repo.create_documentation_item(
                        session_id=session_id,
                        source="scraper",
                        original_job_id=job_id,
                        content=page_chunk.content,
                        page_id=page_chunk.page_id,
                        url=page_chunk.url,
                        summary=page_chunk.summary,
                        metadata=page_chunk.metadata,
                    )
                    # Update chunk ID to match database
                    page_chunk.id = doc_id
                    updated_chunks.append(page_chunk)

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
                    len(page_chunks),
                    len(all_docs),
                )

                logger.info("[Scrape] Job %s: Generating metadata from documentation items", job_id)
                await generate_metadata_from_doc_items(session_id=session_id, db=db)

    else:
        page_chunks = (
            await process_all_pages(
                pages_list,
                app=input.application_name,
                app_version=input.application_version,
                source="scraper",
            )
            if pages_list
            else []
        )

    result = ScrapeResult(
        finish_reason=finish_reason,
        saved_pages_count=len(pages_list),
        page_chunks_count=len(page_chunks),
        saved_pages={str(p.url): p.to_dict() for p in pages_list},
    )

    logger.info(
        "[Scrape] Job %s completed with reason '%s': %s pages, %s chunks, %s irrelevant links",
        job_id,
        finish_reason,
        len(pages_list),
        len(page_chunks),
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
