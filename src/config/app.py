# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Optional

from pydantic import BaseModel


class AppSettings(BaseModel):
    """
    Core application settings for the API service.

    :param title: API title shown in docs.
    :param version: API version string.
    :param description: API description displayed in docs.
    :param api_base_url: Base path for all routes.
    :param host: Host address for Uvicorn server.
    :param port: Port number for Uvicorn server.
    :param live_reload: Enable Uvicorn live reload on changes.
    :param workers: Number of worker processes.
    :param proxy_headers: Trust proxy headers.
    :param forwarded_allow_ips: IPs allowed to be forwarded.
    :param root_path: Root path for mounting.
    :param timeout_keep_alive: Keep-alive timeout for connections.
    :param timeout_graceful_shutdown: Graceful shutdown timeout.
    :param limit_concurrency: Optional limit on concurrent requests.
    :param limit_max_requests: Optional max requests per worker.
    :param ssl_certfile: Optional path to SSL certificate file.
    :param ssl_keyfile: Optional path to SSL key file.
    """

    title: str = "Midpilot Connector Generator"
    version: str = "0.1.0"
    description: str = "Midpilot Connector Generator - discovery, scraping, digester and CodeGen"
    api_base_url: str = "/api"

    host: str = "0.0.0.0"
    port: int = 8090
    live_reload: bool = False
    workers: int = 1
    proxy_headers: bool = True
    forwarded_allow_ips: str = "*"
    root_path: str = ""
    timeout_keep_alive: int = 10
    timeout_graceful_shutdown: int = 15
    limit_concurrency: Optional[int] = None
    limit_max_requests: Optional[int] = None
    ssl_certfile: Optional[str] = None
    ssl_keyfile: Optional[str] = None
