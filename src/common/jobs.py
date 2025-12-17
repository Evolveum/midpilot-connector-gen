# Copyright (c) 2025 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple, Union
from uuid import UUID

from .enums import JobStage, JobStatus
from .session.session import SessionManager

logger = logging.getLogger(__name__)


# TODO
# change file management to database
def _jobs_root() -> Path:
    """Return the jobs root directory for this micro-service.

    By default we store job files under the micro-service package path:
      <repo>/wp1-micro-service/jobs

    Historically, a repository-level jobs directory also existed. To avoid ambiguity
    and to keep the micro-service self-contained, we intentionally resolve to the
    micro-service-local jobs directory (two levels above this file).
    """

    return Path(__file__).resolve().parents[2] / "jobs"


def _ensure_dirs() -> Dict[str, Path]:
    """Ensure the jobs subdirectories exist and return their paths.

    Creates (if missing) the following folders under the jobs root:
    queued/, running/, finished/, failed/
    """
    root = _jobs_root()
    dirs = {
        "queued": root / "queued",
        "running": root / "running",
        "finished": root / "finished",
        "failed": root / "failed",
    }
    for p in dirs.values():
        p.mkdir(parents=True, exist_ok=True)
    return dirs


def _now_iso() -> str:
    """Return current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _serialize_value(obj: Any) -> Any:
    """Convert non-JSON-serializable objects (like UUID) to JSON-compatible types."""
    if isinstance(obj, UUID):
        return str(obj)
    elif isinstance(obj, dict):
        return {key: _serialize_value(value) for key, value in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_serialize_value(item) for item in obj]
    elif hasattr(obj, "model_dump"):
        return obj.model_dump(by_alias=True, mode="json")
    else:
        return obj


def _write(path: Path, data: Dict[str, Any]) -> None:
    """Atomically write JSON `data` to `path` using a same-dir temp file and replace."""
    # Write to a temp file in the same directory and atomically replace
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    serialized_data = _serialize_value(data)
    tmp_path.write_text(json.dumps(serialized_data, ensure_ascii=False, indent=2))
    os.replace(tmp_path, path)


def _read(path: Path) -> Dict[str, Any]:
    """Read and parse JSON from `path` and return the resulting dict."""
    return json.loads(path.read_text())


def _find_job_path(job_id: UUID) -> Tuple[Optional[str], Optional[Path]]:
    """Locate a job file by ID across all job states.

    Returns a tuple of (state, path) where state is one of
    "queued", "running", "finished", "failed"; or (None, None) if not found.
    """
    dirs = _ensure_dirs()
    for state, dir_path in dirs.items():
        f = dir_path / f"{job_id}.json"
        if f.exists():
            return state, f
    return None, None


def update_job_progress(
    job_id: UUID,
    *,
    stage: Optional[Union[str, JobStage]] = None,
    message: Optional[str] = None,
    current_iteration: Optional[int] = None,
    max_iterations: Optional[int] = None,
    total_documents: Optional[int] = None,
    processed_documents: Optional[int] = None,
    current_doc_id: Optional[UUID] = None,
    current_doc_processed_chunks: Optional[int] = None,
    current_doc_total_chunks: Optional[int] = None,
) -> None:
    """Update progress information for a running job."""
    try:
        state, path = _find_job_path(job_id)
        if state is None or path is None:
            return
        data = _read(path)
        prog_val = data.get("progress")
        prog: Dict[str, Any] = dict(prog_val) if isinstance(prog_val, dict) else {}

        # Stage/message
        if stage is not None:
            prog["stage"] = stage.value if isinstance(stage, JobStage) else stage
        if message is not None:
            prog["message"] = message

        # Iterations (existing)
        if current_iteration is not None:
            prog["currentIteration"] = current_iteration
        if max_iterations is not None:
            prog["maxIterations"] = max_iterations

        # Multi-doc top-level
        if total_documents is not None:
            prog["totalDocuments"] = total_documents
        if processed_documents is not None:
            prog["processedDocuments"] = processed_documents

        data["progress"] = prog
        data["updatedAt"] = _now_iso()
        _write(path, data)
    except Exception as e:
        logger.debug("Job progress update failed", exc_info=e)


def increment_processed_documents(job_id: UUID, delta: int = 1) -> None:
    """
    Increment the number of fully processed documents.
    """
    try:
        state, path = _find_job_path(job_id)
        if state is None or path is None:
            return
        data = _read(path)
        prog_val = data.get("progress")
        prog: Dict[str, Any] = dict(prog_val) if isinstance(prog_val, dict) else {}
        current = int(prog.get("processedDocuments") or 0)
        prog["processedDocuments"] = current + delta
        data["progress"] = prog
        data["updatedAt"] = _now_iso()
        _write(path, data)
    except Exception as e:
        logger.debug("Increment processed documents failed.", exc_info=e)


def create_job(input_payload: Dict[str, Any], job_type: str) -> UUID:
    """Create a queued job and return job_id."""
    try:
        _ensure_dirs()
        job_id = uuid.uuid4()
        record = {
            "id": str(job_id),
            "type": job_type,
            "status": JobStatus.queued.value,
            "createdAt": _now_iso(),
            "updatedAt": _now_iso(),
            "input": input_payload,
        }
        queued_path = _jobs_root() / "queued" / f"{job_id}.json"
        _write(queued_path, record)
        return job_id
    except Exception as e:
        logger.debug("Create job failed.", exc_info=e)
        raise


def set_running(job_id: UUID) -> Dict[str, Any]:
    """Transition a queued job to running state and return the updated job record."""
    try:
        state, path = _find_job_path(job_id)
        if state is None or path is None:
            raise FileNotFoundError(f"Job {job_id} not found")
        data = _read(path)
        data["status"] = JobStatus.running.value
        now = _now_iso()
        data.setdefault("startedAt", now)
        data["updatedAt"] = now
        running_path = _jobs_root() / "running" / f"{job_id}.json"
        _write(running_path, data)
        try:
            path.unlink(missing_ok=True)
        finally:
            pass
        return data
    except Exception as e:
        logger.debug("Set job running failed.", exc_info=e)
        return {}


def set_finished(job_id: UUID, result: Dict[str, Any]) -> Dict[str, Any]:
    """Transition a running job to finished state, attach `result`, and return the record."""
    try:
        state, path = _find_job_path(job_id)
        if state is None or path is None:
            raise FileNotFoundError(f"Job {job_id} not found")
        data = _read(path)
        data["status"] = JobStatus.finished.value
        data["updatedAt"] = _now_iso()
        data["result"] = result
        # Ensure progress is updated to finished stage
        prog = dict(data.get("progress") or {})
        prog["stage"] = JobStage.finished.value
        prog["message"] = "completed"
        data["progress"] = prog
        finished_path = _jobs_root() / "finished" / f"{job_id}.json"
        _write(finished_path, data)
        try:
            path.unlink(missing_ok=True)
        finally:
            pass
        return data
    except Exception as e:
        logger.error("Set job to finished failed.", exc_info=e)
        raise


def set_failed(job_id: UUID, error: str) -> Dict[str, Any]:
    """Transition a job to failed state with a normalized list of error messages."""
    state, path = _find_job_path(job_id)
    if state is None or path is None:
        raise FileNotFoundError(f"Job {job_id} not found")
    data = _read(path)
    data["status"] = JobStatus.failed.value
    data["updatedAt"] = _now_iso()
    # Normalize into a structured list of error lines
    lines = [ln for ln in str(error).splitlines() if ln.strip()]
    # Merge with existing errors if any
    existing_list = list(data.get("errors") or [])
    for ln in lines:
        if ln not in existing_list:
            existing_list.append(ln)
    data["errors"] = existing_list or (lines if lines else None)
    data.setdefault("progress", data.get("progress", {}))
    failed_path = _jobs_root() / "failed" / f"{job_id}.json"
    _write(failed_path, data)
    try:
        path.unlink(missing_ok=True)
    finally:
        pass
    return data


def get_job_status(job_id: UUID) -> Dict[str, Any]:
    """Return a public job status dict (id, status, timestamps, progress, result, errors)."""
    try:
        state, path = _find_job_path(job_id)
        if state is None or path is None:
            return {"jobId": job_id, "status": "not_found"}
        data = _read(path)
        public_status = "running" if data.get("status") == JobStatus.running.value else data.get("status")
        out: Dict[str, Any] = {
            "jobId": data.get("id", job_id),
            "status": public_status,
        }
        # Timestamps
        for ts_key in ("createdAt", "startedAt", "updatedAt"):
            if ts_key in data:
                out[ts_key] = data[ts_key]
        # Progress details
        if "progress" in data and isinstance(data["progress"], dict):
            out["progress"] = data["progress"]
        if public_status == JobStatus.finished.value and "result" in data:
            out["result"] = data["result"]
        # Always expose errors if present, even for finished jobs (to surface partial chunk errors)
        if "errors" in data:
            out["errors"] = data["errors"]
        return out
    except Exception as e:
        logger.debug("Get a job failed.", exc_info=e)
        return {}


# TODO
# Refactor this part because "no usage found in all place"
def claim_next_job(job_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Atomically claim the next queued job (optionally filtered by job_type) and mark it as running.
    Safe for multiple worker processes:
      - Each worker lists queued jobs and attempts an atomic os.replace from queued -> running.
      - Only one worker will succeed because the source file disappears immediately upon success.
      - If job_type is specified, we inspect the job before claiming; in case of a race, we simply skip
        if another worker already moved it.
    Returns the claimed job record dict with updated status, or None if no matching jobs are available.
    """
    dirs = _ensure_dirs()
    queued_dir = dirs["queued"]
    running_dir = dirs["running"]

    # Sort by creation time (approx by mtime) for FIFO-like behavior
    files = sorted(queued_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)

    for src in files:
        try:
            # Read to check type (race-safe: if moved meanwhile, FileNotFoundError -> continue)
            try:
                data = _read(src)
            except FileNotFoundError:
                continue

            if job_type and data.get("type") != job_type:
                continue

            # job_id = data.get("id") or src.stem
            dest = running_dir / src.name

            # Attempt to atomically move queued -> running; only one worker will succeed
            try:
                os.replace(src, dest)
            except FileNotFoundError:
                # Another worker claimed it first
                continue

            # Update status in the running file
            try:
                current = _read(dest)
            except FileNotFoundError:
                # Extremely unlikely; if it happens, skip
                continue

            current["status"] = JobStatus.running.value
            current["updatedAt"] = _now_iso()
            _write(dest, current)
            return current
        except Exception:
            # Best-effort; continue to next file
            continue

    return None


def schedule_coroutine_job(
    *,
    job_type: str,
    input_payload: Dict[str, Any],
    worker: Callable[..., Awaitable[Any]],
    worker_args: Optional[Tuple[Any, ...]] = None,
    worker_kwargs: Optional[Dict[str, Any]] = None,
    initial_stage: Optional[Union[str, JobStage]] = None,
    initial_message: Optional[str] = None,
    session_id: Optional[UUID] = None,
    session_result_key: Optional[str] = None,
) -> UUID:
    """
    Create a job record and schedule `worker` coroutine to process it in background.
    The worker must accept the job_id as the last positional argument or via kwarg `job_id` if desired.

    The result of the worker will be auto-serialized:
      - If it has `model_dump`, it's used.
      - Else if it's a Mapping, converted to dict.
      - Otherwise wrapped under {"value": <repr>}.

    If session_id and session_result_key are provided, the result will be automatically
    stored in the session under the given key when the job completes.
    """

    job_id = create_job(input_payload, job_type)

    async def _runner() -> None:
        try:
            set_running(job_id)
            if initial_stage or initial_message:
                update_job_progress(job_id, stage=initial_stage, message=initial_message)

            args = tuple(worker_args or ())
            kwargs = dict(worker_kwargs or {})

            # Prefer explicit kwarg if caller wants to pass it
            if "job_id" in worker.__code__.co_varnames:  # type: ignore[attr-defined]
                kwargs.setdefault("job_id", job_id)

            result = await worker(*args, **kwargs)

            # Auto-serialize result
            result_dict: Dict[str, Any]
            if hasattr(result, "model_dump"):
                result_dict = result.model_dump(by_alias=True)  # type: ignore[attr-defined]
            elif isinstance(result, dict):
                result_dict = result
            else:
                result_dict = {"value": repr(result)}

            # Store result in session if requested (before saving to job)
            if session_id and session_result_key:
                try:
                    # Handle new format with chunks metadata
                    if (
                        isinstance(result_dict, dict)
                        and "result" in result_dict
                        and ("relevant_chunk_indices" in result_dict or "relevantChunks" in result_dict)
                    ):
                        # New format: store result and relevant chunks separately
                        actual_result = result_dict["result"]
                        # Support both old format (relevant_chunk_indices) and new format (relevant_chunks)
                        relevant_chunks = result_dict.get("relevantChunks") or result_dict.get(
                            "relevant_chunk_indices", []
                        )

                        session_updates = {session_result_key: actual_result}

                        # Store relevant chunks for this specific extraction
                        if relevant_chunks:
                            # Get existing relevant_chunks dict or create new one
                            existing_relevant = SessionManager.get_session_data(session_id, "relevantChunks") or {}
                            existing_relevant[session_result_key] = relevant_chunks
                            session_updates["relevantChunks"] = existing_relevant

                        SessionManager.update_session(session_id, session_updates)
                    else:
                        SessionManager.update_session(session_id, {session_result_key: result_dict})
                except Exception:
                    # Don't fail the job if session update fails
                    pass

            # Prepare job result (exclude large chunks array, keep only metadata)
            job_result_dict = result_dict.copy() if isinstance(result_dict, dict) else result_dict
            if isinstance(job_result_dict, dict) and "chunks" in job_result_dict:
                # Remove the large chunks array from job result, keep metadata
                del job_result_dict["chunks"]
                # metadata already contains summary info about chunks

            set_finished(job_id, result=job_result_dict)
        except asyncio.CancelledError as cancel_exc:  # graceful cancellation (e.g., shutdown)
            try:
                set_failed(job_id, error=f"Job cancelled/interrupted: {cancel_exc}")
            except Exception:
                pass
            raise
        except Exception as exc:
            set_failed(job_id, error=str(exc))

    asyncio.create_task(_runner())
    return job_id


def append_job_error(job_id: UUID, message: str) -> None:
    """
    Append a non-fatal error message to the job record without changing its status.
    Used to surface partial/chunk errors while allowing the job to finish successfully.
    """
    state, path = _find_job_path(job_id)
    if state is None or path is None:
        return
    data = _read(path)
    # Update structured list
    errors_list = list(data.get("errors") or [])
    if message not in errors_list:
        errors_list.append(message)
    data["errors"] = errors_list
    data["updatedAt"] = _now_iso()
    _write(path, data)


def recover_stale_running_jobs(note: Optional[str] = None) -> int:
    """
    Move all jobs left in 'running' to 'failed'.
    This is intended to be called on service startup to recover from crashes or hard stops (e.g., CTRL+C).

    :param note: Optional message to include in the error list.
    :return: number of recovered jobs.
    """
    dirs = _ensure_dirs()
    running_dir = dirs["running"]
    count = 0
    for path in list(running_dir.glob("*.json")):
        try:
            data = _read(path)
            job_id = data.get("id") or path.stem
            message = note or "Recovered at startup: previous process stopped while job was running."
            # Reuse set_failed to ensure consistent schema and move the file
            set_failed(UUID(job_id), message)
            count += 1
        except Exception:
            # Best-effort: skip problematic file
            continue
    return count
