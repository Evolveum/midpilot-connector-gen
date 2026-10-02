# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from uuid import UUID

from src.core.errors import AppError


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


class InvalidDocumentationImportError(AppError):
    """Raised when a documentation import payload violates the import contract."""

    status_code = 422
    code = "invalid_documentation_import"


class DocumentationImportConflictError(AppError):
    """Raised when a documentation import collides with existing persisted data.

    The most common cause is a ``chunkId`` that already exists: chunk ids are
    globally unique across sessions. The underlying database message is logged
    rather than returned, so constraint and column names stay internal.
    """

    status_code = 409
    code = "documentation_import_conflict"

    def __init__(self, documentation_id: UUID):
        super().__init__(
            f"Documentation {documentation_id} could not be imported because it conflicts with "
            "existing data. The most likely cause is a chunkId that is already used by another "
            "document; chunk ids must be unique across all sessions."
        )


class SessionCreationFailedError(AppError):
    """Raised when persisting a new session fails for a reason the caller cannot fix.

    midPoint consumes this response in FastAPI's ``{"detail": ...}`` envelope; the
    composition root registers that mapping, so the message is part of the contract.
    """

    status_code = 500
    code = "session_creation_failed"

    def __init__(self) -> None:
        super().__init__("Unable to create session")


class DocumentationUploadRejectedError(AppError):
    """Base for uploaded documentation the service cannot accept.

    Raised both while a request reads the upload and later, while the background
    job parses it; the job runner then records an expected client failure without
    a stack trace. HTTP responses keep FastAPI's ``{"detail": ...}`` envelope
    (registered in the composition root), so subclass messages are part of the
    contract.
    """

    status_code = 422
    code = "documentation_upload_rejected"


class UnsupportedDocumentationFormatError(DocumentationUploadRejectedError):
    """Raised when no parser accepts the upload's content type or file suffix."""

    status_code = 415
    code = "unsupported_documentation_format"

    def __init__(self, content_type: str, filename: str) -> None:
        super().__init__(
            f"Unsupported documentation content type '{content_type}' for {filename}. "
            "Supported uploads include JSON, YAML, OpenAPI, Markdown, AsciiDoc, HTML, XML, CSV, SQL, text, PDF, and DOCX."
        )


class InvalidDocumentationContentError(DocumentationUploadRejectedError):
    """Raised when an upload is empty, unreadable or yields no text."""

    status_code = 422
    code = "invalid_documentation_content"
