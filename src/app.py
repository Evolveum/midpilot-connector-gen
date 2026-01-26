# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .common.jobs import recover_stale_running_jobs
from .config import config
from .router import root_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await recover_stale_running_jobs()
    except Exception:
        pass
    yield


def create_api() -> FastAPI:
    """
    Initialize and configure the FastAPI application.

    :return: Configured FastAPI instance.
    """
    app = FastAPI(title=config.app.title, version="0.1.0", lifespan=lifespan)

    app.include_router(root_router, prefix=f"{config.app.api_base_url}/v1")

    @app.get("/health")
    async def health() -> dict:
        """
        Health check endpoint to verify the service is running.
        """
        return {"message": "OK"}

    return app


api = create_api()
