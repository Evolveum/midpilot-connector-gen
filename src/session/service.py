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

from src.database.repositories.documentation_repository import DocumentationRepository
from src.database.repositories.job_repository import JobRepository
from src.session.errors import DocumentationItemNotFoundError, InvalidDocumentationImportError
from src.session.schema import Documentation

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
    doc_rows_for_document = await doc_repo.get_documentation_items_by_doc_id(session_id, documentation_id)

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


async def import_documentation_document(
    doc_repo: DocumentationRepository,
    session_id: UUID,
    documentation_id: UUID,
    document: Documentation,
) -> int:
    """
    Replace all chunks for one document (``documentation_id``) with the provided
    payload, leaving other documents in the session untouched. Returns the number
    of imported chunks.

    Raises ``InvalidDocumentationImportError`` for a body/path docId mismatch,
    a duplicate chunkId, or an empty/schema-mismatched payload. A DB conflict
    surfaces as ``IntegrityError`` for the router to map to 409.
    """
    if document.doc_id is not None and str(document.doc_id) != str(documentation_id):
        raise InvalidDocumentationImportError(
            f"Body docId ({document.doc_id}) must match path documentation_id ({documentation_id})"
        )

    flat_items: List[Dict[str, Any]] = []
    seen_chunk_ids: set[str] = set()
    for chunk in document.chunks:
        chunk_id = str(chunk.chunk_id)
        if chunk_id in seen_chunk_ids:
            raise InvalidDocumentationImportError(f"Duplicate chunkId in import payload: {chunk_id}")
        seen_chunk_ids.add(chunk_id)

        flat_items.append(
            {
                "chunkId": chunk_id,
                "docId": str(documentation_id),
                "source": chunk.source,
                "url": chunk.url,
                "summary": chunk.summary,
                "content": chunk.content,
                "metadata": chunk.metadata,
                "createdAt": chunk.created_at,
                "scrapeJobIds": [str(job_id) for job_id in chunk.scrape_job_ids],
            }
        )

    if not flat_items:
        if "chunks" in document.model_fields_set:
            logger.warning(
                "Documentation import for doc %s (session %s) rejected: 'chunks' was empty.",
                documentation_id,
                session_id,
            )
            raise InvalidDocumentationImportError("Import payload must include at least one documentation chunk.")

        unexpected_keys = sorted((document.model_extra or {}).keys())
        logger.warning(
            "Documentation import for doc %s (session %s) rejected: request body has no "
            "'chunks' field, so it does not match the expected Documentation schema "
            "(expected {docId?, chunks:[...]}). Unexpected top-level keys: %s",
            documentation_id,
            session_id,
            unexpected_keys or "none",
        )
        raise InvalidDocumentationImportError(
            "Import payload must include a non-empty 'chunks' field matching the Documentation schema. "
            f"Unexpected top-level keys: {unexpected_keys or 'none'}"
        )

    await doc_repo.remove_documentation_items_by_doc_id(session_id, documentation_id)
    await doc_repo.import_documentation_items_for_session(session_id, flat_items)
    logger.info(
        "Imported %d documentation chunk(s) for doc %s (session %s)",
        len(flat_items),
        documentation_id,
        session_id,
    )
    return len(flat_items)


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


def build_group_documentation_response(doc_rows: list[dict[str, Any]]) -> list[Documentation]:
    """
    Group flat documentation rows by logical document (source + docId/chunkId fallback).
    """
    bundles_by_key: dict[tuple[str, str], dict[str, Any]] = {}

    for item in doc_rows:
        doc_identity = item["docId"] or item["chunkId"]
        key = (item["source"], doc_identity)

        bundle = bundles_by_key.get(key)
        if bundle is None:
            bundle = {
                "docId": item["docId"],
                "chunks": [],
            }
            bundles_by_key[key] = bundle

        bundle["chunks"].append(
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
        )

    return [Documentation.model_validate(bundle) for bundle in bundles_by_key.values()]
