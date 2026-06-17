import os
import signal
import sys
from concurrent.futures import ProcessPoolExecutor


def handle_sigint(signum, frame):
    """Catch the signal and exit the worker cleanly."""
    sys.exit(0)


def init_worker():
    signal.signal(signal.SIGINT, handle_sigint)


def create_pool() -> ProcessPoolExecutor:
    return ProcessPoolExecutor(
        max_workers=os.cpu_count(),
        initializer=init_worker,
    )


process_pool: ProcessPoolExecutor | None = None
