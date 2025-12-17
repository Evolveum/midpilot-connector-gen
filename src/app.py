# Copyright (c) 2025 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .common.jobs import recover_stale_running_jobs
from .config import config
from .router import root_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: best-effort recovery of stale running jobs
    try:
        recover_stale_running_jobs()  # sync function; call directly
        # If this were async: `await recover_stale_running_jobs()`
    except Exception:
        # Best-effort recovery; do not block startup
        pass

    # Hand control to the app
    yield

    # (Optional) Shutdown logic goes here
    # e.g., close connections, flush metrics, etc.


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
