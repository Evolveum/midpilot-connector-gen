# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Persistence of a finished job's result into its session.

Stores the (relevance-stripped) result under the configured session key and replaces the
session's relevant-chunk rows for that key. Failures here are non-fatal for the job: they
are logged and recorded as a job error so the job can still finish.
"""

import logging
from typing import Any, Dict
from uuid import UUID

from src.common.database.config import async_session_maker
from src.common.database.repositories.documentation_repository import DocumentationRepository
from src.common.database.repositories.relevant_chunk_repository import RelevantChunkRepository
from src.common.database.repositories.session_repository import SessionRepository
from src.common.jobs import lifecycle
from src.common.utils.relevance import (
    build_chunk_to_doc_map as _build_chunk_to_doc_map,
)
from src.common.utils.relevance import (
    extract_relevant_rows_for_storage as _extract_relevant_rows_for_storage,
)
from src.common.utils.relevance import (
    strip_relevance_from_session_payload as _strip_relevance_from_session_payload,
)
from src.common.utils.relevance import (
    unwrap_result_payload as _unwrap_result_payload,
)

logger = logging.getLogger(__name__)


async def persist_result_to_session(
    *,
    job_id: UUID,
    session_id: UUID,
    session_result_key: str,
    result_dict: Any,
    input_payload: Dict[str, Any],
) -> None:
    """Store the job result under ``session_result_key`` and refresh relevant-chunk rows.

    Errors are caught, logged and appended to the job's error list so the job can still be
    marked finished.
    """
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
            await lifecycle._append_job_error_now(job_id, error_msg)
        except Exception:
            logger.error("Failed to record session persistence error for job %s", job_id, exc_info=True)
