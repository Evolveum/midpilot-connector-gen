# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Session persistence for digester extraction side-effects.

Keeps the objectClassesOutput write-back out of the extraction workflows: the
extractors produce a result, this layer persists a derived field (attributes /
endpoints) onto the matching object class. Best-effort by design — a persistence
failure is logged, never raised, so it cannot fail the extraction job.
"""

import logging
from typing import Any
from uuid import UUID

from src.core.errors import JobClaimLostError
from src.modules.digester.entities.object_classes import ObjectClassResultField, update_object_class_field_in_session

logger = logging.getLogger(__name__)


async def persist_object_class_field(
    session_id: UUID,
    object_class: str,
    field_name: ObjectClassResultField,
    field_value: Any,
    logger_scope: str,
) -> None:
    """
    Write an extracted field (e.g. ``"attributes"`` / ``"endpoints"``) back onto the
    object class in ``objectClassesOutput``.

    Failures are logged and swallowed: a missing object class or a persistence error
    must not fail the extraction job that produced the value.
    """
    try:
        updated = await update_object_class_field_in_session(
            session_id=session_id,
            object_class=object_class,
            field_name=field_name,
            field_value=field_value,
        )
        if not updated:
            logger.warning("[%s] Failed to update objectClassesOutput for %s", logger_scope, object_class)
    except JobClaimLostError:
        raise
    except Exception:
        logger.exception("[%s] Failed to persist %s for %s", logger_scope, field_name, object_class)
