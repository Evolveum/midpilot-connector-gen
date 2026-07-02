# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from uuid import UUID


class AppError(Exception):
    """
    Base class for domain errors that map to an HTTP response.

    Carries the HTTP status code and a stable, machine-readable error code as
    data rather than coupling the domain layer to FastAPI. The mapping to an
    HTTP response is done centrally in src.common.exception_handlers, so
    routers and services only need to raise these exceptions.
    """

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ObjectClassesNotFoundError(AppError):
    """Raised when a session has no object classes available for a requested operation."""

    status_code = 404
    code = "object_classes_not_found"

    def __init__(self, session_id: UUID | None = None):
        session_context = f" in session {session_id}" if session_id else " in session"
        super().__init__(f"No object classes found{session_context}. Please run /classes endpoint first.")


class InvalidObjectClassesOutputError(AppError):
    """Raised when objectClassesOutput exists but does not match the expected contract."""

    status_code = 422
    code = "invalid_object_classes_output"

    def __init__(self, session_id: UUID):
        super().__init__(f"Invalid object classes data in session {session_id}")


class ObjectClassNotFoundError(AppError):
    """Raised when a requested object class cannot be found in session data."""

    status_code = 404
    code = "object_class_not_found"

    def __init__(self, object_class: str, session_id: UUID):
        super().__init__(f"Object class '{object_class}' not found in session {session_id}")


class RelevantChunksNotFoundError(AppError):
    """Raised when an extraction cannot proceed because no relevant chunks were selected."""

    status_code = 400
    code = "relevant_chunks_not_found"

    def __init__(self, object_class: str, extraction_target: str):
        super().__init__(
            f"No relevant chunks found for object class '{object_class}'. Cannot extract {extraction_target}."
        )


class SessionNotFoundError(AppError):
    """Raised when a session does not exist."""

    status_code = 404
    code = "session_not_found"

    def __init__(self, session_id: UUID):
        super().__init__(f"Session {session_id} not found")


class SessionAlreadyExistsError(AppError):
    """Raised when creating a session with an ID that is already taken."""

    status_code = 409
    code = "session_already_exists"

    def __init__(self, session_id: UUID):
        super().__init__(f"Session {session_id} already exists")


class DocumentationNotFoundError(AppError):
    """Raised when a session has no documentation at all."""

    status_code = 404
    code = "documentation_not_found"

    def __init__(self, session_id: UUID):
        super().__init__(f"No documentation found in session {session_id}")


class DocumentationItemNotFoundError(AppError):
    """Raised when a specific documentation document cannot be found in a session."""

    status_code = 404
    code = "documentation_item_not_found"

    def __init__(self, documentation_id: UUID, session_id: UUID):
        super().__init__(f"Documentation {documentation_id} not found in session {session_id}")


class NoDocumentationStoredError(AppError):
    """Raised when an operation needs documentation but none has been stored yet."""

    status_code = 400
    code = "no_documentation_stored"

    def __init__(self, session_id: UUID):
        super().__init__(
            f"Session {session_id} has no stored documentation. Please upload documentation file or run scraper."
        )


class JobNotFoundError(AppError):
    """Raised when a referenced job cannot be found in a session."""

    status_code = 404
    code = "job_not_found"

    def __init__(self, job_label: str, session_id: UUID, detail: str | None = None):
        super().__init__(detail or f"No {job_label} job found in session {session_id}")


class AttributesNotFoundError(AppError):
    """Raised when attributes for an object class have not been extracted yet."""

    status_code = 404
    code = "attributes_not_found"

    def __init__(self, object_class: str, session_id: UUID):
        super().__init__(
            f"No attributes found for {object_class} in session {session_id}. "
            f"Please run /classes/{object_class}/attributes endpoint first."
        )


class OperationSurfaceNotFoundError(AppError):
    """Raised when the endpoints/table metadata needed to generate operations are missing.

    The message is protocol-dependent (REST endpoints vs SQL table metadata) and is
    therefore supplied by the caller rather than built here.
    """

    status_code = 404
    code = "operation_surface_not_found"

    def __init__(self, message: str):
        super().__init__(message)


class RelationsNotFoundError(AppError):
    """Raised when a session has no extracted relations."""

    status_code = 404
    code = "relations_not_found"

    def __init__(self, session_id: UUID):
        super().__init__(f"No relations found in session {session_id}. Please run /relations endpoint first.")


class RelationNotFoundError(AppError):
    """Raised when a specific relation cannot be found in a session."""

    status_code = 404
    code = "relation_not_found"

    def __init__(self, relation_name: str, session_id: UUID):
        super().__init__(f"Relation {relation_name} not found in session {session_id}.")


class InvalidRelationsOutputError(AppError):
    """Raised when stored relationsOutput exists but does not match the expected contract."""

    status_code = 422
    code = "invalid_relations_output"

    def __init__(self, session_id: UUID):
        super().__init__(
            f"Stored relationsOutput is invalid in session {session_id}. "
            "Re-run relations extraction or override the relations payload."
        )
