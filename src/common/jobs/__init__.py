# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Public background job API."""

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
