# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from hypercorn import Config
from hypercorn.run import run as run_hypercorn

from src.common.logger import get_configured_log_level_name, setup_logging
from src.config import config

hypercorn_error_logger = setup_logging()


def build_hypercorn_config() -> Config:
    hc_config = Config()
    hc_config.application_path = "src.app:api"
    hc_config.bind = [f"{config.app.host}:{config.app.port}"]
    hc_config.certfile = config.app.ssl_certfile
    hc_config.keyfile = config.app.ssl_keyfile
    hc_config.root_path = config.app.root_path
    hc_config.keep_alive_timeout = config.app.timeout_keep_alive
    hc_config.graceful_timeout = config.app.timeout_graceful_shutdown
    hc_config.max_requests = config.app.limit_max_requests
    hc_config.accesslog = "-" if config.logging.access_log else None
    hc_config.errorlog = hypercorn_error_logger
    hc_config.loglevel = get_configured_log_level_name()
    hc_config.use_reloader = config.app.live_reload
    hc_config.workers = config.app.workers if (config.app.live_reload or config.app.workers > 1) else 0

    return hc_config


def run_application():
    run_hypercorn(build_hypercorn_config())


if __name__ == "__main__":
    run_application()
