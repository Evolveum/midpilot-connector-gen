#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

from typing import Optional
from uuid import UUID

from .enums import JobStatus
from .jobs import get_job_status
from .schema import (
    BaseProgress,
    JobStatusStageResponse,
)


async def build_stage_status_response(job_id: UUID) -> JobStatusStageResponse:
    """Build a stage-only status response (stage + message)."""
    status = await get_job_status(job_id)
    raw_status = status.get("status", JobStatus.not_found.value)
    enum_status = JobStatus(raw_status)
    prog = status.get("progress") or {}
    progress: Optional[BaseProgress] = None
    if isinstance(prog, dict) and ("stage" in prog or "message" in prog):
        progress = BaseProgress(stage=prog.get("stage"), message=prog.get("message"))

    return JobStatusStageResponse(
        jobId=status.get("jobId", job_id),
        status=enum_status,
        createdAt=status.get("createdAt"),
        startedAt=status.get("startedAt"),
        updatedAt=status.get("updatedAt"),
        progress=progress,
        result=status.get("result"),
        errors=status.get("errors"),
    )
