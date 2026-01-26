# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import uvicorn

from src.common.logger import setup_logging
from src.config import config

setup_logging()


def run_application():
    uvicorn.run(
        "src.app:api",
        host=config.app.host,
        port=config.app.port,
        reload=config.app.live_reload,
        workers=config.app.workers,
        proxy_headers=config.app.proxy_headers,
        forwarded_allow_ips=config.app.forwarded_allow_ips,
        root_path=config.app.root_path,
        timeout_keep_alive=config.app.timeout_keep_alive,
        timeout_graceful_shutdown=config.app.timeout_graceful_shutdown,
        limit_concurrency=config.app.limit_concurrency,
        limit_max_requests=config.app.limit_max_requests,
        ssl_certfile=config.app.ssl_certfile,
        ssl_keyfile=config.app.ssl_keyfile,
        access_log=config.logging.access_log,
        log_level=config.logging.level.value,
    )


if __name__ == "__main__":
    run_application()
