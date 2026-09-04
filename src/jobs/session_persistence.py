# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Persistence of scheduled-job pointers and finished results into a session.

Scheduled jobs and their session pointers are written in the caller-owned transaction. Finished
jobs store their relevance-stripped result under the configured session key and replace the
relevant-chunk rows for that key.
"""

import logging
from typing import Any, Dict
from uuid import UUID

from src.core.db import async_session_maker
from src.core.job_execution import get_current_execution
from src.database.repositories.job_repository import JobRepository
from src.database.repositories.relevant_chunk_repository import RelevantChunkRepository
from src.database.repositories.session_repository import SessionRepository
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
from src.jobs.errors import JobClaimLostError
from src.jobs.result_envelope import get_session_companion_outputs, missing_session_companion_outputs

logger = logging.getLogger(__name__)


async def persist_job_pointer(
    repo: SessionRepository,
    session_id: UUID,
    key_prefix: str,
    session_input: Dict[str, Any],
    job_id: UUID,
) -> None:
    """
    Persist a scheduled job's pointer using the shared naming convention:
    ``{key_prefix}JobId`` (stringified job id) and ``{key_prefix}Input``.

    The job row and pointer remain in the caller's transaction so HTTP request dependencies can
    commit them together after response validation and non-HTTP callers can choose their own
    transaction boundary.
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
    required_companion_keys: tuple[str, ...] = (),
) -> bool:
    """Store the primary and companion outputs and refresh primary relevance rows.

    All session values are published in one transaction. Failures are recorded on the job
    and re-raised so execution cannot finish without every promised output.
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

            missing_companions = missing_session_companion_outputs(result_dict, required_companion_keys)
            if missing_companions:
                raise ValueError(
                    "Worker result is missing required session companion output(s): " + ", ".join(missing_companions)
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
                companion_outputs = get_session_companion_outputs(result_dict)
                if session_result_key in companion_outputs:
                    raise ValueError(f"Companion outputs cannot replace primary result {session_result_key!r}")
                persisted = await repo.update_results_if_current_job(
                    session_id=session_id,
                    result_key=session_result_key,
                    job_id=job_id,
                    values={session_result_key: session_payload, **companion_outputs},
                )
                if not persisted:
                    await db.rollback()
                    return False

                relevant_rows = _extract_relevant_rows_for_storage(
                    result_dict,
                    result_key=session_result_key,
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
            logger.exception("Failed to record session persistence error for job %s", job_id)
        raise
