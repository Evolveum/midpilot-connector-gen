# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from src.api.exception_handlers import register_exception_handlers
from src.auth.dependencies import authenticate_request
from src.config import config
from src.core import pool
from src.core.llm import aclose_llm_http_client
from src.jobs import recover_stale_running_jobs
from src.router import root_router
from src.session.ownership import enforce_session_ownership

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        pool.process_pool = pool.create_pool()
    except Exception:
        logger.exception("Failed to create process pool during startup")
        raise

    try:
        await recover_stale_running_jobs()
    except Exception:
        logger.exception("Failed to recover stale running jobs during startup")

    try:
        yield
    finally:
        await aclose_llm_http_client()
        if pool.process_pool:
            pool.process_pool.shutdown(wait=True)


def create_api() -> FastAPI:
    """
    Initialize and configure the FastAPI application.

    :return: Configured FastAPI instance.
    """
    app = FastAPI(title=config.app.title, version="0.1.0", lifespan=lifespan)

    register_exception_handlers(app)

    # API key auth + session ownership run for every API route; /health stays open.
    # Order matters: authentication stores the AuthContext the ownership check reads.
    app.include_router(
        root_router,
        prefix=f"{config.app.api_base_url}/v1",
        dependencies=[Depends(authenticate_request), Depends(enforce_session_ownership)],
    )

    @app.get("/health")
    async def health() -> dict:
        """
        Health check endpoint to verify the service is running.
        """
        return {"message": "OK"}

    return app


api = create_api()
