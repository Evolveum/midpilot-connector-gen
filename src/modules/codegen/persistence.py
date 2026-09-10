# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Session persistence for manual codegen overrides.

Thin write-through helpers used by the override (PUT) endpoints to store
user-provided Groovy code into the session, keyed by operation. Kept separate
from job orchestration: these only persist, they neither schedule nor run work.
"""

import logging
from typing import Mapping
from uuid import UUID

from src.core.db import async_session_maker
from src.core.job_execution import get_current_execution
from src.database.repositories.job_repository import JobRepository
from src.database.repositories.session_repository import SessionRepository
from src.modules.codegen.enums import SearchIntent, build_search_operation_key
from src.modules.codegen.schema import GroovyCodePayload

logger = logging.getLogger(__name__)


async def store_authorization_override(
    repo: SessionRepository,
    session_id: UUID,
    code: GroovyCodePayload,
) -> None:
    await repo.update_session(session_id, {"authorizationOutput": code.model_dump()})


async def store_object_class_output_override(
    repo: SessionRepository,
    session_id: UUID,
    object_class: str,
    operation_name: str,
    code: GroovyCodePayload,
) -> None:
    await repo.update_session(session_id, {f"{object_class}{operation_name}Output": code.model_dump()})


async def store_search_override(
    repo: SessionRepository,
    session_id: UUID,
    object_class: str,
    intent: SearchIntent,
    code: GroovyCodePayload,
) -> None:
    operation_key = build_search_operation_key(object_class, intent)
    await repo.update_session(session_id, {f"{operation_key}Output": code.model_dump()})


async def store_relation_override(
    repo: SessionRepository,
    session_id: UUID,
    relation_name: str,
    code: GroovyCodePayload,
) -> None:
    await repo.update_session(session_id, {f"{relation_name}CodeOutput": code.model_dump()})


async def store_fixed_connector_scripts(
    session_id: UUID,
    updates: Mapping[str, GroovyCodePayload],
    *,
    job_id: UUID,
) -> None:
    """
    Write every repaired operation script of an object-class fix back to the session.

    One ``update_session`` call, so the fix lands as a unit: the session is either
    fully updated or untouched. Looping over the per-operation override helpers
    would re-derive keys the caller already holds and would leave a partially
    applied fix behind if one write failed.

    Last write wins: unlike the per-operation results, these writes are not
    checked against the ``{key}JobId`` pointers, so a fix that overlaps a
    generation or an override of the same operation silently overwrites it.
    """
    if not updates:
        return

    async with async_session_maker() as db:
        execution = get_current_execution()
        if execution is not None:
            # Rejects a stale execution of this fix job only. A concurrent write to
            # the same {key}Output is not covered: no {key}JobId pointer names the
            # fix job, so update_result_if_current_job cannot be used here.
            await JobRepository(db).acquire_execution_fence(
                job_id,
                worker_id=execution.worker_id,
                execution_token=execution.execution_token,
            )
        await SessionRepository(db).update_session(
            session_id, {key: payload.model_dump() for key, payload in updates.items()}
        )
        await db.commit()

    logger.info("[Codegen:Fix] Persisted %d repaired connector script(s)", len(updates))
