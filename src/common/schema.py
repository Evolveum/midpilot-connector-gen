# Copyright (c) 2025 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .enums import JobStatus


def validate_pydantic_object(obj: Any, model: Any) -> Any:
    """
    Check if an object is a valid Pydantic model instance.
    This method is used in two scenarios:
    1. To validate if a single object conforms to a Pydantic model.
    2. To convert and validate any object to a Pydantic model instance.
    inputs:
        obj - object to validate
        model - Pydantic model class
    output:
        Adapted object if valid, else False
    """

    # This is a really ugly design - using try except to validate URL but pydantic does not seem to have a simple validate function
    try:
        validator = TypeAdapter(model)
        return validator.validate_python(obj)
    except Exception:
        return False


# --- Job Models ---


class JobCreateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jobId: UUID = Field(..., description="Unique identifier of the created job.")


class BaseProgress(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    stage: Optional[str] = Field(default=None, description="High-level stage, e.g., running, finished")
    message: Optional[str] = Field(default=None, description="Human-friendly note about current work")


class ChunkProgress(BaseProgress):
    processedChunks: Optional[int] = Field(default=None, description="Number of processed chunks")
    totalChunks: Optional[int] = Field(default=None, description="Total number of chunks")


class IterationProgress(BaseProgress):
    currentIteration: Optional[int] = Field(default=None, description="Current iteration (1-based)")
    maxIterations: Optional[int] = Field(default=None, description="Maximum iterations configured")


class DocumentProgress(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    docId: Optional[UUID] = Field(default=None, description="Identifier of the document being processed")
    processedChunks: Optional[int] = Field(default=None, description="Chunks processed in the current document")
    totalChunks: Optional[int] = Field(default=None, description="Total chunks for the current document")


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


# --- End Job Models ---
