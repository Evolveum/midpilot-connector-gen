# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from src.api.exception_handlers import register_exception_handlers
from src.auth.dependencies import authenticate_request
from src.config import config
from src.core import pool
from src.core.db import close_db
from src.core.llm import aclose_llm_http_client
from src.jobs import JobWorker
from src.router import root_router
from src.session.ownership import enforce_session_ownership

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    job_worker: JobWorker | None = None
    if config.jobs.enabled:
        try:
            pool.process_pool = pool.create_pool(config.jobs.cpu_processes)
        except Exception:
            logger.exception("Failed to create process pool during startup")
            raise
        job_worker = JobWorker()
        job_worker.start()
        app.state.job_worker = job_worker

    try:
        yield
    finally:
        if job_worker is not None:
            await job_worker.stop()
        await aclose_llm_http_client()
        if pool.process_pool:
            await asyncio.to_thread(pool.process_pool.shutdown, wait=True, cancel_futures=True)
            pool.process_pool = None
        await close_db()


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
