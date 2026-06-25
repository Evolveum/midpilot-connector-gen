# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Background job package.

Public facade re-exporting the job lifecycle operations and the background runner so that
existing ``from src.common.jobs import ...`` call sites keep working after the split into
focused submodules:

- :mod:`src.common.jobs.futures` - in-process task/future registry
- :mod:`src.common.jobs.lifecycle` - job state transitions and status
- :mod:`src.common.jobs.cache` - previous-output reuse
- :mod:`src.common.jobs.session_persistence` - persisting results into the session
- :mod:`src.common.jobs.runner` - background orchestration entrypoint
"""

from src.common.jobs.futures import _job_futures as _job_futures
from src.common.jobs.lifecycle import (
    append_job_error,
    create_job,
    get_job_status,
    increment_processed_documents,
    recover_stale_running_jobs,
    set_failed,
    set_finished,
    set_running,
    update_job_progress,
)
from src.common.jobs.runner import schedule_coroutine_job

__all__ = [
    "append_job_error",
    "create_job",
    "get_job_status",
    "increment_processed_documents",
    "recover_stale_running_jobs",
    "schedule_coroutine_job",
    "set_failed",
    "set_finished",
    "set_running",
    "update_job_progress",
]
