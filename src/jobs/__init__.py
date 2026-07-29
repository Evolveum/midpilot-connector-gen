# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Public background job API."""

from src.jobs.lifecycle import (
    append_job_error,
    get_job_status,
    increment_processed_documents,
    set_failed,
    set_finished,
    update_job_progress,
)
from src.jobs.payload import job_input_reference
from src.jobs.runner import schedule_coroutine_job
from src.jobs.session_persistence import persist_job_pointer
from src.jobs.worker import JobWorker

__all__ = [
    "append_job_error",
    "get_job_status",
    "increment_processed_documents",
    "JobWorker",
    "job_input_reference",
    "persist_job_pointer",
    "schedule_coroutine_job",
    "set_failed",
    "set_finished",
    "update_job_progress",
]
