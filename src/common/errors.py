#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

from fastapi import HTTPException


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
