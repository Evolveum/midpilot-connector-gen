# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from uuid import UUID

from src.core.errors import AppError


class JobNotFoundError(AppError):
    """Raised when a referenced job cannot be found in a session."""

    status_code = 404
    code = "job_not_found"

    def __init__(self, job_label: str, session_id: UUID, detail: str | None = None):
        super().__init__(detail or f"No {job_label} job found in session {session_id}")
