import signal
import sys
from concurrent.futures import ProcessPoolExecutor


def handle_sigint(signum, frame):
    """Catch the signal and exit the worker cleanly."""
    sys.exit(0)


def init_worker():
    signal.signal(signal.SIGINT, handle_sigint)


def create_pool(max_workers: int) -> ProcessPoolExecutor:
    return ProcessPoolExecutor(
        max_workers=max_workers,
        initializer=init_worker,
    )


process_pool: ProcessPoolExecutor | None = None
