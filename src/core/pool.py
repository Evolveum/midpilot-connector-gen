import multiprocessing
import signal
import sys
from concurrent.futures import ProcessPoolExecutor


def handle_sigint(signum, frame):
    """Catch the signal and exit the worker cleanly."""
    sys.exit(0)


def init_worker():
    signal.signal(signal.SIGINT, handle_sigint)


def create_pool(max_workers: int) -> ProcessPoolExecutor:
    # Not Linux's default fork: a forked worker keeps the server's client sockets open, so connections the
    # server closes never send FIN and the proxy's next request on them hangs until its timeout.
    return ProcessPoolExecutor(
        max_workers=max_workers,
        mp_context=multiprocessing.get_context("forkserver"),
        initializer=init_worker,
    )


process_pool: ProcessPoolExecutor | None = None


def require_process_pool() -> ProcessPoolExecutor:
    """Return the configured CPU pool or fail instead of using asyncio's thread pool."""
    if process_pool is None:
        raise RuntimeError("CPU process pool is not initialized in this worker process")
    return process_pool
