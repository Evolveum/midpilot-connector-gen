# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from hypercorn import Config
from hypercorn.run import run as run_hypercorn

from src.common.logger import setup_logging
from src.config import config

setup_logging()


def build_hypercorn_config() -> Config:
    hc_config = Config()
    hc_config.application_path = "src.app:api"
    # No ssl_certfile/keyfile -> Hypercorn serves plaintext HTTP/1.1 and h2c (HTTP/2 cleartext,
    # via prior-knowledge or the Upgrade: h2c header) on the same bind. Set both to enable
    # TLS/ALPN HTTP/2 on this same port later, e.g. behind an external reverse proxy.
    hc_config.bind = [f"{config.app.host}:{config.app.port}"]
    hc_config.certfile = config.app.ssl_certfile
    hc_config.keyfile = config.app.ssl_keyfile
    hc_config.root_path = config.app.root_path
    hc_config.keep_alive_timeout = config.app.timeout_keep_alive
    hc_config.graceful_timeout = config.app.timeout_graceful_shutdown
    hc_config.max_requests = config.app.limit_max_requests
    hc_config.accesslog = "-" if config.logging.access_log else None
    hc_config.loglevel = config.logging.level.value.upper()

    hc_config.use_reloader = config.app.live_reload
    # Hypercorn's worker pool (separate processes, spawned via multiprocessing) only starts
    # when workers >= 1; workers == 0 runs the single worker directly in this process, which
    # matches Uvicorn's previous single-worker behavior and avoids the extra process by default.
    # The reloader requires the worker pool, so it forces workers >= 1.
    hc_config.workers = config.app.workers if (config.app.live_reload or config.app.workers > 1) else 0

    return hc_config


def run_application():
    run_hypercorn(build_hypercorn_config())


if __name__ == "__main__":
    run_application()
