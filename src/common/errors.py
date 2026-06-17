# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from uuid import UUID

from fastapi import HTTPException


class ObjectClassesNotFoundError(LookupError):
    """Raised when a session has no object classes available for a requested operation."""

    def __init__(self, session_id: UUID | None = None):
        session_context = f" in session {session_id}" if session_id else " in session"
        super().__init__(f"No object classes found{session_context}. Please run /classes endpoint first.")


class InvalidObjectClassesOutputError(ValueError):
    """Raised when objectClassesOutput exists but does not match the expected contract."""

    def __init__(self, session_id: UUID):
        super().__init__(f"Invalid object classes data in session {session_id}")


class ObjectClassNotFoundError(LookupError):
    """Raised when a requested object class cannot be found in session data."""

    def __init__(self, object_class: str, session_id: UUID):
        super().__init__(f"Object class '{object_class}' not found in session {session_id}")


class RelevantChunksNotFoundError(ValueError):
    """Raised when an extraction cannot proceed because no relevant chunks were selected."""

    def __init__(self, object_class: str, extraction_target: str):
        super().__init__(
            f"No relevant chunks found for object class '{object_class}'. Cannot extract {extraction_target}."
        )


class LLMResponseValidationException(HTTPException):
    """
    Exception raised when an LLM response fails validation.
    """

    def __init__(self):
        super().__init__(status_code=500, detail="LLM Response Validation Error")


class NotImplementedError(HTTPException):
    """
    Exception raised for functionality not being implemented yet.
    """

    def __init__(self):
        super().__init__(status_code=501, detail="Not Implemented")
