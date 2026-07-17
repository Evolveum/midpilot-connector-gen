# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Session persistence for manual codegen overrides.

Thin write-through helpers used by the override (PUT) endpoints to store
user-provided Groovy code into the session, keyed by operation. Kept separate
from job orchestration: these only persist, they neither schedule nor run work.
"""

from uuid import UUID

from src.common.database.repositories.session_repository import SessionRepository
from src.modules.codegen.enums import SearchIntent, build_search_operation_key
from src.modules.codegen.schema import GroovyCodePayload


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
