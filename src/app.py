# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src import pool
from src.common.exception_handlers import register_exception_handlers
from src.common.jobs import recover_stale_running_jobs
from src.config import config
from src.router import root_router

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
        if pool.process_pool:
            pool.process_pool.shutdown(wait=True)


def create_api() -> FastAPI:
    """
    Initialize and configure the FastAPI application.

    :return: Configured FastAPI instance.
    """
    app = FastAPI(title=config.app.title, version="0.1.0", lifespan=lifespan)

    register_exception_handlers(app)

    app.include_router(root_router, prefix=f"{config.app.api_base_url}/v1")

    @app.get("/health")
    async def health() -> dict:
        """
        Health check endpoint to verify the service is running.
        """
        return {"message": "OK"}

    return app


api = create_api()
