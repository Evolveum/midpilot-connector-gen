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

from src.core.db import async_session_maker
from src.core.errors import JobClaimLostError
from src.core.job_execution import get_current_execution
from src.database.repositories.documentation_repository import DocumentationRepository
from src.database.repositories.job_repository import JobRepository
from src.database.repositories.relevant_chunk_repository import RelevantChunkRepository
from src.database.repositories.session_repository import SessionRepository
from src.documents.relevance import (
    build_chunk_to_doc_map as _build_chunk_to_doc_map,
)
from src.documents.relevance import (
    extract_relevant_rows_for_storage as _extract_relevant_rows_for_storage,
)
from src.documents.relevance import (
    strip_relevance_from_session_payload as _strip_relevance_from_session_payload,
)
from src.documents.relevance import (
    unwrap_result_payload as _unwrap_result_payload,
)
from src.jobs import lifecycle

logger = logging.getLogger(__name__)


async def persist_job_pointer(
    repo: SessionRepository,
    session_id: UUID,
    key_prefix: str,
    session_input: Dict[str, Any],
    job_id: UUID,
) -> None:
    """
    Persist a scheduled job's pointer onto the session using the shared naming
    convention: ``{key_prefix}JobId`` (stringified job id) and ``{key_prefix}Input``.

    Centralizes the schedule-then-track pattern shared by the codegen and digester
    orchestration layers so the key naming stays defined in one place.
    """
    await repo.update_session(
        session_id,
        {
            f"{key_prefix}JobId": str(job_id),
            f"{key_prefix}Input": session_input,
        },
    )


async def persist_result_to_session(
    *,
    job_id: UUID,
    session_id: UUID,
    session_result_key: str,
    result_dict: Any,
    input_payload: Dict[str, Any],
) -> bool:
    """Store the job result under ``session_result_key`` and refresh relevant-chunk rows.

    Failures are recorded on the job and re-raised so the execution cannot be
    reported as finished without its promised session result.
    """
    try:
        async with async_session_maker() as db:
            repo = SessionRepository(db)
            relevant_repo = RelevantChunkRepository(db)
            execution = get_current_execution()
            if execution is not None:
                await JobRepository(db).acquire_execution_fence(
                    job_id,
                    worker_id=execution.worker_id,
                    execution_token=execution.execution_token,
                )

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
                persisted = await repo.update_result_if_current_job(
                    session_id=session_id,
                    result_key=session_result_key,
                    job_id=job_id,
                    value=session_payload,
                )
                if not persisted:
                    await db.rollback()
                    return False

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
                persisted = await repo.update_result_if_current_job(
                    session_id=session_id,
                    result_key=session_result_key,
                    job_id=job_id,
                    value=result_dict,
                )
                if not persisted:
                    await db.rollback()
                    return False

            await db.commit()
            return True
    except JobClaimLostError:
        raise
    except Exception as e:
        error_msg = f"Session persistence failed for job {job_id} in session {session_id}: {e}"
        logger.error(error_msg, exc_info=e)
        try:
            await lifecycle._append_job_error_now(job_id, error_msg)
        except JobClaimLostError:
            raise
        except Exception:
            logger.error("Failed to record session persistence error for job %s", job_id, exc_info=True)
        raise
