# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Session access helpers shared by routers and feature modules.

Existence guards, session-scoped job resolution and documentation reads.
These are pure lookups - authorization (session ownership) is enforced
centrally in src.session.ownership.
"""

import uuid
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import async_session_maker
from src.database.repositories.documentation_repository import DocumentationRepository
from src.database.repositories.job_repository import JobRepository
from src.database.repositories.session_repository import SessionRepository
from src.documents.errors import NoDocumentationStoredError
from src.jobs.errors import JobNotFoundError
from src.session.documentation_upload import read_uploaded_documentation
from src.session.errors import SessionNotFoundError


async def get_session_documentation(
    session_id: UUID, documentation: UploadFile | None = None, db: AsyncSession | None = None
) -> list[dict]:
    """
    Helper to get all documentation items from session or uploaded file.
    Can be imported by other module routers.
    Returns list of documentation items with their UUIDs and content.
    """
    if db is None:
        async with async_session_maker() as session:
            return await _get_session_documentation_impl(session_id, documentation, session)
    else:
        return await _get_session_documentation_impl(session_id, documentation, db)


async def _get_session_documentation_impl(
    session_id: UUID, documentation: UploadFile | None, db: AsyncSession
) -> list[dict]:
    repo = SessionRepository(db)
    if not await repo.session_exists(session_id):
        raise SessionNotFoundError(session_id)

    doc_repo = DocumentationRepository(db)

    if documentation is not None:
        uploaded = await read_uploaded_documentation(documentation)

        doc_id = uuid.uuid4()
        chunk_id = await doc_repo.create_documentation_item(
            session_id=session_id,
            source="upload",
            content=uploaded.text,
            doc_id=doc_id,
            url=None,
            summary=None,
            metadata=uploaded.metadata,
        )

        await db.commit()
        return [
            {
                "chunkId": str(chunk_id),
                "docId": str(doc_id),
                "source": "upload",
                "scrapeJobIds": [],
                "url": None,
                "summary": None,
                "content": uploaded.text,
                "@metadata": uploaded.metadata,
            }
        ]

    doc_items = await doc_repo.get_documentation_items_by_session(session_id)
    if doc_items:
        return [
            {
                "chunkId": item.get("chunkId"),
                "docId": item.get("docId"),
                "source": item.get("source"),
                "scrapeJobIds": item.get("scrapeJobIds", []),
                "url": item.get("url"),
                "summary": item.get("summary"),
                "content": item.get("content", ""),
                "@metadata": item.get("metadata", {}) or {},
            }
            for item in doc_items
        ]

    raise NoDocumentationStoredError(session_id)


async def resolve_session_job_id(
    repo: SessionRepository,
    session_id: UUID,
    job_id: UUID | None,
    session_key: str,
    job_label: str,
    not_found_detail: str | None = None,
) -> UUID:
    if isinstance(job_id, UUID):
        if await JobRepository(repo.db).get_job_for_session(job_id, session_id) is None:
            raise JobNotFoundError(job_label, session_id, detail=not_found_detail)
        return job_id

    job_id_value = await repo.get_session_data(session_id, session_key)
    if not job_id_value:
        raise JobNotFoundError(job_label, session_id, detail=not_found_detail)

    return job_id_value if isinstance(job_id_value, UUID) else UUID(str(job_id_value))


async def ensure_session_exists(repo: SessionRepository, session_id: UUID) -> None:
    if not await repo.session_exists(session_id):
        raise SessionNotFoundError(session_id)
