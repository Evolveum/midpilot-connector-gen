# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from uuid import uuid4

import pytest

from src.jobs.payload import (
    InvalidJobPayloadError,
    binary_artifact_reference,
    build_execution_payload,
    deserialize_call,
    job_input_reference,
    resolve_callable,
    serialize_value,
    validate_call_arguments,
)
from src.modules.discovery.schema import CandidateLinksInput
from src.modules.discovery.service import discover_candidate_links
from src.session.documentation_processing import process_documentation_worker
from src.session.schema import RawUploadedDocumentation


async def required_argument_worker(required: int) -> None:
    pass


def test_pydantic_request_round_trips_through_execution_payload() -> None:
    session_id = uuid4()
    request = CandidateLinksInput(application_name="Example")
    payload = build_execution_payload(
        worker=discover_candidate_links,
        worker_args=(request, session_id),
        worker_kwargs={},
        dynamic_input_provider=None,
        session_result_key="discoveryOutput",
        await_documentation=False,
        await_documentation_timeout=None,
    )

    worker = resolve_callable(payload["worker"])
    args, kwargs = deserialize_call(worker, payload["args"], payload["kwargs"])

    assert isinstance(args[0], CandidateLinksInput)
    assert args[0].application_name == "Example"
    assert args[1] == session_id
    assert kwargs == {}


def test_raw_upload_bytes_and_dataclass_round_trip_through_execution_payload() -> None:
    session_id = uuid4()
    doc_id = uuid4()
    raw_upload = RawUploadedDocumentation(
        data=b"binary\x00payload",
        filename="schema.pdf",
        content_type="application/pdf",
        content_hash="abc",
    )
    payload = build_execution_payload(
        worker=process_documentation_worker,
        worker_args=(),
        worker_kwargs={
            "session_id": session_id,
            "raw_upload": {
                "data": binary_artifact_reference("raw-upload"),
                "filename": raw_upload.filename,
                "content_type": raw_upload.content_type,
                "content_hash": raw_upload.content_hash,
            },
            "doc_id": doc_id,
            "app": "Example",
            "app_version": "1",
        },
        dynamic_input_provider=None,
        session_result_key=None,
        await_documentation=False,
        await_documentation_timeout=None,
        binary_artifacts={"raw-upload": raw_upload.data},
    )

    assert payload["kwargs"]["raw_upload"]["data"]["name"] == "raw-upload"
    assert "binary\\u0000payload" not in str(payload)
    worker = resolve_callable(payload["worker"])
    args, kwargs = deserialize_call(
        worker,
        payload["args"],
        payload["kwargs"],
        artifacts={"raw-upload": raw_upload.data},
    )

    assert args[0] == session_id
    assert isinstance(args[1], RawUploadedDocumentation)
    assert args[1].data == b"binary\x00payload"
    assert args[2] == doc_id
    assert args[3:] == ("Example", "1")
    assert kwargs == {}


def test_equal_bytes_are_not_implicitly_replaced_with_an_artifact_reference() -> None:
    value = b"interned"

    serialized = serialize_value(value, artifact_names={"raw-upload"})

    assert serialized["__midpilot_job_payload_type__"] == "bytes-base64"
    assert serialized["data"] == "aW50ZXJuZWQ="


def test_final_call_validation_rejects_missing_required_arguments() -> None:
    with pytest.raises(InvalidJobPayloadError, match="complete call"):
        validate_call_arguments(required_argument_worker, (), {})


def test_worker_argument_can_reference_large_value_in_job_input() -> None:
    session_id = uuid4()
    payload = build_execution_payload(
        worker=discover_candidate_links,
        worker_args=(job_input_reference(), session_id),
        worker_kwargs={},
        dynamic_input_provider=None,
        session_result_key="discoveryOutput",
        await_documentation=False,
        await_documentation_timeout=None,
    )
    job_input = {"applicationName": "Example", "maxCandidateLinks": 7}

    worker = resolve_callable(payload["worker"])
    args, _ = deserialize_call(
        worker,
        payload["args"],
        payload["kwargs"],
        job_input=job_input,
    )

    assert isinstance(args[0], CandidateLinksInput)
    assert args[0].application_name == "Example"
    assert args[0].max_candidate_links == 7
    assert "Example" not in str(payload["args"][0])
