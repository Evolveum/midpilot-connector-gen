# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Response and progress models for background jobs."""

from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.shared.enums import JobStatus


class JobCreateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jobId: UUID = Field(..., description="Unique identifier of the created job.")


class BaseProgress(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    stage: Optional[str] = Field(default=None, description="High-level stage, e.g., running, finished")
    message: Optional[str] = Field(default=None, description="Human-friendly note about current work")


class IterationProgress(BaseProgress):
    completedIterations: Optional[int] = Field(default=None, description="Current iteration")
    totalIterations: Optional[int] = Field(default=None, description="Maximum iterations configured")


class MultiDocProgress(BaseProgress):
    processedDocuments: Optional[int] = Field(default=None, description="Number of fully processed documents")
    totalDocuments: Optional[int] = Field(default=None, description="Total number of documents to process")


class BaseJobStatusResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jobId: UUID = Field(..., description="Job identifier")
    status: JobStatus = Field(..., description="Current status of the job")
    createdAt: Optional[str] = Field(default=None, description="Job creation time (ISO 8601)")
    startedAt: Optional[str] = Field(default=None, description="Job start time (ISO 8601)")
    updatedAt: Optional[str] = Field(default=None, description="Last update time (ISO 8601)")
    result: Optional[Any] = Field(
        default=None,
        description="Result payload when status is 'finished'",
    )
    errors: Optional[list[str]] = Field(
        default=None,
        description=(
            "Structured list of error lines/messages for pretty presentation. "
            "Each list item is a single error line. Backward-compatible with 'error' string."
        ),
    )


class JobStatusStageResponse(BaseJobStatusResponse):
    progress: Optional[BaseProgress] = Field(default=None, description="Stage + message only progress info")


class JobStatusIterationResponse(BaseJobStatusResponse):
    progress: Optional[IterationProgress] = Field(
        default=None, description="Iteration-based progress info (current/max)"
    )


class JobStatusMultiDocResponse(BaseJobStatusResponse):
    progress: Optional[MultiDocProgress] = Field(
        default=None, description="Multi-document progress (documents + current document chunk progress)"
    )
