# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Optional

from pydantic import BaseModel, Field


class AppSettings(BaseModel):
    """
    Core application settings for the API service.

    :param title: API title shown in docs.
    :param version: API version string.
    :param description: API description displayed in docs.
    :param api_base_url: Base path for all routes.
    :param host: Host address for the Hypercorn server.
    :param port: Port number for the Hypercorn server.
    :param live_reload: Enable Hypercorn live reload on changes.
    :param workers: Number of worker processes.
    :param root_path: Root path for mounting.
    :param timeout_keep_alive: Keep-alive timeout for connections.
    :param timeout_graceful_shutdown: Graceful shutdown timeout.
    :param limit_max_requests: Optional max requests per worker before it is recycled.
    :param ssl_certfile: Optional path to SSL certificate file. Serving stays plaintext
        HTTP/1.1 + h2c until this and ssl_keyfile are both set.
    :param ssl_keyfile: Optional path to SSL key file.
    """

    title: str = "Midpilot Connector Generator"
    version: str = "0.1.0"
    description: str = "Midpilot Connector Generator - discovery, scraping, digester and codegen"
    api_base_url: str = "/api"

    host: str = "0.0.0.0"
    port: int = 8090
    live_reload: bool = False
    workers: int = Field(default=1, ge=1)
    root_path: str = ""
    timeout_keep_alive: int = 10
    timeout_graceful_shutdown: int = 15
    limit_max_requests: Optional[int] = None
    ssl_certfile: Optional[str] = None
    ssl_keyfile: Optional[str] = None
