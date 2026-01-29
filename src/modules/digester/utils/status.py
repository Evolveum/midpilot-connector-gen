# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any, Type
from uuid import UUID

from ....common.enums import JobStatus
from ....common.jobs import get_job_status
from ....common.schema import JobStatusMultiDocResponse
from ..schema import ObjectClassesResponse


async def build_typed_job_status_response(job_id: UUID, model_cls: Type[Any]) -> JobStatusMultiDocResponse:
    """Normalize JobStatusMultiDocResponse and parse the result into a given model."""
    status = await get_job_status(job_id)
    result_payload = None
    raw_status = status.get("status", JobStatus.not_found.value)
    if raw_status == JobStatus.finished.value and isinstance(status.get("result"), dict):
        try:
            result_dict = status["result"]
            # Handle new format with chunks metadata
            if "result" in result_dict and isinstance(result_dict["result"], dict):
                actual_result = result_dict["result"]
            else:
                actual_result = result_dict

            # Special handling for ObjectClassesResponse to ensure proper model validation
            if model_cls == ObjectClassesResponse:
                # Ensure objectClasses is a list and each item has the required fields
                if "objectClasses" in actual_result and isinstance(actual_result["objectClasses"], list):
                    for obj_class in actual_result["objectClasses"]:
                        # Ensure relevant_chunks exists and is a list
                        if "relevant_chunks" not in obj_class:
                            obj_class["relevant_chunks"] = []
                        elif not isinstance(obj_class["relevant_chunks"], list):
                            obj_class["relevant_chunks"] = []

            if hasattr(model_cls, "model_validate"):
                result_payload = model_cls.model_validate(actual_result)
            else:
                result_payload = model_cls(**actual_result)
        except Exception as exc:
            return JobStatusMultiDocResponse(
                jobId=status.get("jobId", job_id),
                status=JobStatus.failed,
                errors=[f"Corrupted result payload: {str(exc)}"],
            )
    enum_status = JobStatus(raw_status)

    return JobStatusMultiDocResponse(
        jobId=status.get("jobId", job_id),
        status=enum_status,
        createdAt=status.get("createdAt"),
        startedAt=status.get("startedAt"),
        updatedAt=status.get("updatedAt"),
        progress=status.get("progress"),
        result=result_payload,
        errors=status.get("errors"),
    )
