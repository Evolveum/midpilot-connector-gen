# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Domain errors raised while reading or selecting stored documentation."""

from uuid import UUID

from src.core.errors import AppError


class DocumentationUploadSupersededError(AppError):
    """A completed upload no longer owns the document's publication pointer."""

    status_code = 409
    code = "documentation_upload_superseded"

    def __init__(self) -> None:
        super().__init__(
            "Upload was superseded by a newer upload or its session was removed; no document was replaced."
        )


class NoDocumentationStoredError(AppError):
    """Raised when an operation needs documentation but none has been stored yet.

    Lives here rather than next to the session errors because the condition is
    about the documentation, and the selection code that detects it sits below
    the session layer.
    """

    status_code = 400
    code = "no_documentation_stored"

    def __init__(self, session_id: UUID):
        super().__init__(
            f"Session {session_id} has no stored documentation. Please upload documentation file or run scraper."
        )
