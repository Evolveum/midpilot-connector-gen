# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Serialization boundary for durable background-job execution payloads."""

import base64
import copy
import dataclasses
import importlib
import inspect
from collections.abc import Collection
from datetime import date, datetime
from enum import Enum
from typing import Any, Callable, Mapping, Sequence, get_type_hints
from uuid import UUID

from pydantic import BaseModel, TypeAdapter

_TYPE_TAG = "__midpilot_job_payload_type__"
_BYTES_TYPE = "bytes-base64"
_ARTIFACT_TYPE = "binary-artifact"
_JOB_INPUT_TYPE = "job-input-reference"
_PAYLOAD_VERSION = 1


class InvalidJobPayloadError(ValueError):
    """Raised when a persisted execution payload cannot be safely executed."""


@dataclasses.dataclass(frozen=True)
class JobInputReference:
    """Reference a value already stored in ``jobs.input``."""

    path: tuple[str | int, ...]


@dataclasses.dataclass(frozen=True)
class BinaryArtifactReference:
    """Reference a named binary value stored outside the JSONB payload."""

    name: str


def job_input_reference(*path: str | int) -> JobInputReference:
    """Build a typed reference to the job input root or one of its descendants."""
    return JobInputReference(path=tuple(path))


def binary_artifact_reference(name: str) -> BinaryArtifactReference:
    """Build an explicit reference to a named durable-job artifact."""
    if not name:
        raise InvalidJobPayloadError("Binary artifact reference name must not be empty")
    return BinaryArtifactReference(name=name)


def callable_reference(callable_: Callable[..., Any]) -> str:
    """Return a stable import reference for a top-level callable."""
    module_name = getattr(callable_, "__module__", None)
    qualname = getattr(callable_, "__qualname__", None)
    if not module_name or not qualname or "<locals>" in qualname:
        raise InvalidJobPayloadError("Background-job callables must be importable top-level functions")
    return f"{module_name}:{qualname}"


def resolve_callable(reference: str) -> Callable[..., Any]:
    """Resolve a persisted ``module:qualname`` callable reference."""
    try:
        module_name, qualname = reference.split(":", 1)
        value: Any = importlib.import_module(module_name)
        for part in qualname.split("."):
            value = getattr(value, part)
    except (ImportError, AttributeError, ValueError) as exc:
        raise InvalidJobPayloadError(f"Cannot resolve background-job callable {reference!r}") from exc
    if not callable(value):
        raise InvalidJobPayloadError(f"Background-job target {reference!r} is not callable")
    return value


def serialize_value(
    value: Any,
    *,
    artifact_names: Collection[str] = (),
) -> Any:
    """Convert an execution argument to a JSONB-safe value."""
    if isinstance(value, JobInputReference):
        return {
            _TYPE_TAG: _JOB_INPUT_TYPE,
            "path": list(value.path),
        }
    if isinstance(value, BinaryArtifactReference):
        if value.name not in artifact_names:
            raise InvalidJobPayloadError(f"Missing binary job artifact {value.name!r}")
        return {
            _TYPE_TAG: _ARTIFACT_TYPE,
            "name": value.name,
        }
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {
            _TYPE_TAG: _BYTES_TYPE,
            "data": base64.b64encode(value).decode("ascii"),
        }
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return serialize_value(value.value, artifact_names=artifact_names)
    if isinstance(value, BaseModel):
        return serialize_value(value.model_dump(by_alias=True, mode="python"), artifact_names=artifact_names)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: serialize_value(getattr(value, field.name), artifact_names=artifact_names)
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): serialize_value(item, artifact_names=artifact_names) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [serialize_value(item, artifact_names=artifact_names) for item in value]
    raise InvalidJobPayloadError(f"Unsupported background-job argument type: {type(value).__qualname__}")


def _resolve_job_input_reference(job_input: Mapping[str, Any], path: Sequence[str | int]) -> Any:
    current: Any = job_input
    for component in path:
        try:
            current = current[component]
        except (KeyError, IndexError, TypeError) as exc:
            raise InvalidJobPayloadError(f"Job input reference path {list(path)!r} does not exist") from exc
    return copy.deepcopy(current)


def deserialize_value(
    value: Any,
    *,
    job_input: Mapping[str, Any] | None = None,
    artifacts: Mapping[str, bytes] | None = None,
) -> Any:
    """Restore tagged values which cannot be represented directly by JSONB."""
    if isinstance(value, list):
        return [deserialize_value(item, job_input=job_input, artifacts=artifacts) for item in value]
    if isinstance(value, dict):
        payload_type = value.get(_TYPE_TAG)
        if payload_type == _BYTES_TYPE:
            encoded = value.get("data")
            if not isinstance(encoded, str):
                raise InvalidJobPayloadError("Invalid base64 bytes payload")
            try:
                return base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise InvalidJobPayloadError("Invalid base64 bytes payload") from exc
        if payload_type == _ARTIFACT_TYPE:
            artifact_name = value.get("name")
            if not isinstance(artifact_name, str) or artifacts is None or artifact_name not in artifacts:
                raise InvalidJobPayloadError(f"Missing binary job artifact {artifact_name!r}")
            return artifacts[artifact_name]
        if payload_type == _JOB_INPUT_TYPE:
            path = value.get("path")
            if (
                job_input is None
                or not isinstance(path, list)
                or not all(isinstance(component, (str, int)) for component in path)
            ):
                raise InvalidJobPayloadError("Invalid job input reference")
            return _resolve_job_input_reference(job_input, path)
        return {key: deserialize_value(item, job_input=job_input, artifacts=artifacts) for key, item in value.items()}
    return value


def build_execution_payload(
    *,
    worker: Callable[..., Any],
    worker_args: tuple[Any, ...],
    worker_kwargs: dict[str, Any],
    dynamic_input_provider: Callable[..., Any] | None,
    session_result_key: str | None,
    await_documentation: bool,
    await_documentation_timeout: float | None,
    session_companion_result_keys: Sequence[str] = (),
    binary_artifacts: Mapping[str, bytes] | None = None,
) -> dict[str, Any]:
    """Build the versioned JSONB execution contract stored with a job."""
    artifact_names = frozenset((binary_artifacts or {}).keys())
    return {
        "version": _PAYLOAD_VERSION,
        "worker": callable_reference(worker),
        "args": serialize_value(worker_args, artifact_names=artifact_names),
        "kwargs": serialize_value(worker_kwargs, artifact_names=artifact_names),
        "dynamicInputProvider": (
            callable_reference(dynamic_input_provider) if dynamic_input_provider is not None else None
        ),
        "sessionResultKey": session_result_key,
        "sessionCompanionResultKeys": list(session_companion_result_keys),
        "awaitDocumentation": await_documentation,
        "awaitDocumentationTimeout": await_documentation_timeout,
    }


def validate_execution_payload(payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if payload is None or payload.get("version") != _PAYLOAD_VERSION:
        raise InvalidJobPayloadError("Unsupported or missing background-job execution payload")
    if not isinstance(payload.get("worker"), str):
        raise InvalidJobPayloadError("Background-job execution payload has no worker")
    if not isinstance(payload.get("args"), list) or not isinstance(payload.get("kwargs"), dict):
        raise InvalidJobPayloadError("Background-job execution arguments are invalid")
    companion_keys = payload.get("sessionCompanionResultKeys", [])
    if not isinstance(companion_keys, list) or any(
        not isinstance(key, str) or not key.endswith("Output") for key in companion_keys
    ):
        raise InvalidJobPayloadError("Background-job companion result keys are invalid")
    return payload


def deserialize_call(
    callable_: Callable[..., Any],
    raw_args: Any,
    raw_kwargs: Any,
    *,
    job_input: Mapping[str, Any] | None = None,
    artifacts: Mapping[str, bytes] | None = None,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Deserialize and validate persisted arguments against a callable signature."""
    args = tuple(deserialize_value(raw_args, job_input=job_input, artifacts=artifacts))
    kwargs = dict(deserialize_value(raw_kwargs, job_input=job_input, artifacts=artifacts))
    signature = inspect.signature(callable_)
    try:
        bound = signature.bind_partial(*args, **kwargs)
    except TypeError as exc:
        raise InvalidJobPayloadError(f"Persisted arguments do not match {callable_reference(callable_)}") from exc

    try:
        type_hints = get_type_hints(callable_)
    except (NameError, TypeError):
        type_hints = {}

    for name, value in list(bound.arguments.items()):
        parameter = signature.parameters[name]
        if parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        annotation = type_hints.get(name, parameter.annotation)
        if annotation is inspect.Parameter.empty or annotation is Any:
            continue
        # ``TypeAdapter`` handles UUIDs, enums, Pydantic models and dataclasses.
        # Fail explicitly when a deploy changed a callable contract in a way
        # that is incompatible with an already queued payload.
        try:
            bound.arguments[name] = TypeAdapter(annotation).validate_python(value)
        except Exception as exc:
            raise InvalidJobPayloadError(
                f"Invalid persisted value for argument {name!r} of {callable_reference(callable_)}"
            ) from exc

    return tuple(bound.args), dict(bound.kwargs)


def validate_call_arguments(
    callable_: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: Mapping[str, Any],
) -> None:
    """Validate the complete call after runtime arguments have been added."""
    try:
        inspect.signature(callable_).bind(*args, **kwargs)
    except TypeError as exc:
        raise InvalidJobPayloadError(
            f"Persisted arguments do not provide a complete call to {callable_reference(callable_)}"
        ) from exc
