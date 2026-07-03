# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Digester endpoints for V2 API (session-centric).

Thin aggregator: the actual endpoints live in ``routes/`` split per resource
(object classes, attributes, endpoints, relations, connectivity endpoint, auth,
metadata). Paths and responses are unchanged; only the file organization differs.
"""

from fastapi import APIRouter

from src.modules.digester.routes import (
    attributes,
    auth,
    connectivity_endpoint,
    endpoints,
    metadata,
    object_classes,
    relations,
)

router = APIRouter()

router.include_router(object_classes.router)
router.include_router(attributes.router)
router.include_router(endpoints.router)
router.include_router(relations.router)
router.include_router(connectivity_endpoint.router)
router.include_router(auth.router)
router.include_router(metadata.router)
