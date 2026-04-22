import os
from concurrent.futures import ProcessPoolExecutor

# def _init_worker():
#     os.sched_setaffinity(0, set(range(os.cpu_count())))  # ← reset regardless of parent

def create_pool() -> ProcessPoolExecutor:
    return ProcessPoolExecutor(
        max_workers=os.cpu_count(),
        #initializer=_init_worker
    )

process_pool: ProcessPoolExecutor | None = None
