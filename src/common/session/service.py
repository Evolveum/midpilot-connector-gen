# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Session data orchestration.

Holds the multi-step data-access flows and documentation payload transformations
behind the session endpoints, so the router stays a thin HTTP adapter. Depends on
the repositories and raises domain errors (``AppError``) for the HTTP layer to map.
"""

import logging
from typing import Any, Dict, List
from uuid import UUID

from src.common.database.repositories.documentation_repository import DocumentationRepository
from src.common.database.repositories.job_repository import JobRepository
from src.common.errors import DocumentationItemNotFoundError
from src.common.session.schema import Documentation

logger = logging.getLogger(__name__)

_UPLOAD_JOB_TYPE_PREFIX = "documentation.processUpload"


async def list_documentation_upload_jobs(job_repo: JobRepository, session_id: UUID) -> List[Dict[str, Any]]:
    """Return the session's documentation-upload jobs, filtered by job type."""
    jobs = await job_repo.get_jobs_by_session(session_id)
    return [job for job in jobs if job.get("type", "").startswith(_UPLOAD_JOB_TYPE_PREFIX)]


async def get_documentation_document(
    doc_repo: DocumentationRepository, session_id: UUID, documentation_id: UUID
) -> Documentation:
    """
    Return one documentation document (all chunks for ``documentation_id``) in the
    export bundle shape. Raises ``DocumentationItemNotFoundError`` when it is absent.
    """
    doc_rows = await doc_repo.get_documentation_items_for_export(session_id)
    doc_rows_for_document = [item for item in doc_rows if str(item.get("docId")) == str(documentation_id)]

    if not doc_rows_for_document:
        raise DocumentationItemNotFoundError(documentation_id, session_id)

    document_payload = {
        "docId": str(documentation_id),
        "chunks": [
            {
                "chunkId": item["chunkId"],
                "source": item["source"],
                "url": item["url"],
                "summary": item["summary"],
                "content": item["content"],
                "metadata": item["metadata"],
                "createdAt": item["createdAt"],
                "scrapeJobIds": item["scrapeJobIds"],
            }
            for item in doc_rows_for_document
        ],
    }
    return Documentation.model_validate(document_payload)


async def delete_documentation_document(
    doc_repo: DocumentationRepository, session_id: UUID, documentation_id: UUID
) -> int:
    """
    Delete all chunks for ``documentation_id``, detaching related job ids first.

    Returns the number of removed chunks. Raises ``DocumentationItemNotFoundError``
    when no chunks exist for the document.
    """
    doc_items_with_doc_id = await doc_repo.get_documentation_items_by_doc_id(session_id, documentation_id)
    source = doc_items_with_doc_id[0].get("source") if doc_items_with_doc_id else ""
    if not source:
        logger.warning(
            "Could not determine source for documentation with doc_id %s in session %s", documentation_id, session_id
        )
    else:
        await doc_repo.remove_job_ids_from_documentation_items(session_id, source)

    if not doc_items_with_doc_id:
        raise DocumentationItemNotFoundError(documentation_id, session_id)

    return await doc_repo.remove_documentation_items_by_doc_id(session_id, documentation_id)
