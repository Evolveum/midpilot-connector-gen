# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.


class AppError(Exception):
    """
    Base class for domain errors that map to an HTTP response.

    Carries the HTTP status code and a stable, machine-readable error code as
    data rather than coupling the domain layer to FastAPI. The mapping to an
    HTTP response is done centrally in src.api.exception_handlers, so routers
    and services only need to raise these exceptions.

    Domain-specific subclasses live next to the domain that raises them (e.g.
    src.auth.errors, src.session.errors); only technology-level errors that
    belong to no domain are defined here.
    """

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class LLMUnavailableError(AppError):
    """
    Raised when the language-model backend is unreachable (connection/timeout failure).

    Distinguishes an infrastructure outage (the model server being down, e.g. VPN not
    connected or the GPU host not responding) from a per-item content failure. This lets a
    background job fail explicitly with a clear, machine-readable signal the GUI can render,
    instead of silently degrading to an empty/scaffold result reported as a finished job.
    """

    status_code = 503
    code = "llm_unavailable"

    def __init__(self, context: str = ""):
        detail = f" while {context}" if context else ""
        super().__init__(
            f"The language model service is currently unreachable{detail}. "
            "This is usually a temporary connectivity problem with the server "
            "Please verify the connection and try again."
        )


class JobClaimLostError(RuntimeError):
    """Signal that a durable-job execution no longer owns the current claim."""

    def __init__(self, job_id: object):
        super().__init__(f"Execution claim for job {job_id} is no longer current")
        self.job_id = job_id
