# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Standalone durable-job consumer for worker-only Kubernetes deployments."""

import asyncio
import logging
import signal

from src.config import config
from src.core import pool
from src.core.db import close_db
from src.core.llm import aclose_llm_http_client
from src.jobs.worker import JobWorker

logger = logging.getLogger(__name__)


async def run_worker_until_stopped() -> None:
    """Run one queue consumer process until SIGINT or SIGTERM."""
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop_event.set)
        except NotImplementedError:  # pragma: no cover - Windows event loops
            pass

    worker = JobWorker()
    try:
        pool.process_pool = pool.create_pool(config.jobs.cpu_processes)
        worker.start()
        while not stop_event.is_set():
            if not worker.is_running:
                raise RuntimeError("Standalone durable-job worker stopped unexpectedly")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=1)
            except asyncio.TimeoutError:
                pass
    finally:
        await worker.stop()
        await aclose_llm_http_client()
        if pool.process_pool is not None:
            await asyncio.to_thread(pool.process_pool.shutdown, wait=True, cancel_futures=True)
            pool.process_pool = None
        await close_db()


def main() -> None:
    """CLI entry point used by a worker-only container command."""
    logger.info("Starting standalone durable-job worker")
    asyncio.run(run_worker_until_stopped())


if __name__ == "__main__":
    main()
