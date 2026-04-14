# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any, Optional, Type
from uuid import UUID

from src.common.enums import JobStatus
from src.common.jobs import get_job_status
from src.common.schema import (
    BaseProgress,
    JobStatusMultiDocResponse,
    JobStatusStageResponse,
)
from src.common.session.schema import Documentation


async def build_stage_status_response(job_id: UUID | None) -> JobStatusStageResponse:
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


async def build_multi_doc_status_response(job_id: UUID | None) -> JobStatusMultiDocResponse:
    """
    Build a multi-document aware status response for codegen jobs.
    It forwards the progress dict as-is so multi-doc fields (processedDocuments,
    totalDocuments, currentDocument{docId, processedChunks, totalChunks}) are preserved.
    """
    status = await get_job_status(job_id)
    raw_status = status.get("status", JobStatus.not_found.value)
    enum_status = JobStatus(raw_status)

    return JobStatusMultiDocResponse(
        jobId=status.get("jobId", job_id),
        status=enum_status,
        createdAt=status.get("createdAt"),
        startedAt=status.get("startedAt"),
        updatedAt=status.get("updatedAt"),
        progress=status.get("progress"),
        result=status.get("result"),
        errors=status.get("errors"),
    )


async def build_typed_job_status_response(job_id: UUID, model_cls: Type[Any]) -> JobStatusMultiDocResponse:
    """Build multi-doc status response and parse successful result into the provided model class."""
    status = await get_job_status(job_id)
    raw_status = status.get("status", JobStatus.not_found.value)
    result_payload = None

    if raw_status == JobStatus.finished.value and isinstance(status.get("result"), dict):
        try:
            result_dict = status["result"]
            if "result" in result_dict and isinstance(result_dict["result"], dict):
                actual_result = dict(result_dict["result"])
            else:
                actual_result = dict(result_dict)

            # Some payloads may omit this optional list; normalize for robust model validation.
            if isinstance(actual_result.get("objectClasses"), list):
                for obj_class in actual_result["objectClasses"]:
                    if isinstance(obj_class, dict):
                        relevant = obj_class.get("relevantDocumentations")
                        if not isinstance(relevant, list):
                            obj_class["relevantDocumentations"] = []

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


def build_group_documentation_response(doc_rows: list[dict[str, Any]]) -> list[Documentation]:
    """
    Group flat documentation rows by logical document (source + docId/chunkId fallback).
    """
    bundles_by_key: dict[tuple[str, str], dict[str, Any]] = {}

    for item in doc_rows:
        doc_identity = item["docId"] or item["chunkId"]
        key = (item["source"], doc_identity)

        bundle = bundles_by_key.get(key)
        if bundle is None:
            bundle = {
                "docId": item["docId"],
                "chunks": [],
            }
            bundles_by_key[key] = bundle

        bundle["chunks"].append(
            {
                "chunkId": item["chunkId"],
                "source": item["source"],
                "url": item["url"],
                "summary": item["summary"],
                "content": item["content"],
                "metadata": item["metadata"],
                "createdAt": item["createdAt"],
                "scrapeJobIds": item["scrapeJobIds"],
            }
        )

    return [Documentation.model_validate(bundle) for bundle in bundles_by_key.values()]
