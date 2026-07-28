# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from fastapi import APIRouter

from src.auth.router import router as api_keys_router
from src.modules.codegen.router import router as codegen_router
from src.modules.digester.router import router as digester_router
from src.modules.discovery.router import router as discovery_router
from src.modules.scrape.router import router as scrape_router
from src.session.routes.documentation import router as session_documentation_router
from src.session.routes.sessions import router as session_router

root_router = APIRouter()

"""
Root API router that aggregates all sub-module routers under their respective prefixes and tags.
"""

# API key management (master key only)
root_router.include_router(api_keys_router, prefix="/apiKeys", tags=["API Keys"])

# Session management (sessions + session-scoped documentation share the /session prefix)
root_router.include_router(session_router, prefix="/session", tags=["Session"])
root_router.include_router(session_documentation_router, prefix="/session", tags=["Session"])

root_router.include_router(discovery_router, prefix="/discovery", tags=["Discovery"])
root_router.include_router(scrape_router, prefix="/scrape", tags=["Scrape"])
root_router.include_router(digester_router, prefix="/digester")
root_router.include_router(codegen_router, prefix="/codegen")
