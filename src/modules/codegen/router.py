# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Codegen endpoints for V2 API (session-centric).

Thin aggregator: the actual endpoints live in ``routes/`` split per resource
(authorization, native-schema, connid, search, create/update/delete, relations,
and object-class fixes).
"""

from fastapi import APIRouter

from src.modules.codegen.routes import (
    authorization,
    connid,
    fix,
    native_schema,
    operations,
    relations,
    search,
)

router = APIRouter()

router.include_router(authorization.router)
router.include_router(native_schema.router)
router.include_router(connid.router)
router.include_router(search.router)
router.include_router(operations.router)
router.include_router(relations.router)
router.include_router(fix.router)
