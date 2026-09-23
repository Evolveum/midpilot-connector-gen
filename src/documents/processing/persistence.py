# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Atomic publication shared by freshly processed and cached uploads."""

import logging
from collections.abc import Sequence
from uuid import UUID

from src.core.db import async_session_maker
from src.database.repositories.documentation_repository import DocumentationChunkWrite, DocumentationRepository
from src.documents.errors import DocumentationUploadSupersededError

logger = logging.getLogger(__name__)


async def publish_uploaded_documentation(
    *,
    session_id: UUID,
    doc_id: UUID,
    job_id: UUID,
    filename: str,
    chunks: Sequence[DocumentationChunkWrite],
) -> None:
    async with async_session_maker() as db:
        published = await DocumentationRepository(db).replace_uploaded_documentation(
            session_id=session_id, doc_id=doc_id, job_id=job_id, filename=filename, chunks=chunks
        )
        if not published:
            logger.warning(
                "[Documents:Upload] Upload for document %s is no longer current; publication skipped", doc_id
            )
            raise DocumentationUploadSupersededError()
        await db.commit()
