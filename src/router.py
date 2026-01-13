#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

from fastapi import APIRouter

from .common.session.router import router as session_router
from .modules.codegen.router import router as codegen_router
from .modules.digester.router import router as digester_router
from .modules.discovery.router import router as discovery_router
from .modules.scrape.router import router as scrape_router

root_router = APIRouter()

"""
Root API router that aggregates all sub-module routers under their respective prefixes and tags.
"""

# Session management
root_router.include_router(session_router, prefix="/session", tags=["Session"])

# Include each endpoint router with a prefix and optional tags
root_router.include_router(discovery_router, prefix="/discovery", tags=["Discovery"])
root_router.include_router(scrape_router, prefix="/scrape", tags=["Scrape"])
root_router.include_router(digester_router, prefix="/digester", tags=["Digester"])
root_router.include_router(codegen_router, prefix="/codegen", tags=["CodeGen"])
