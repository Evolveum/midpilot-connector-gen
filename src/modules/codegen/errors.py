# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Errors raised by codegen.

Domain errors live next to the domain that raises them. Codegen additionally
imports the digester's result errors (the one allowed cross-module dependency:
codegen -> digester) for problems that originate in extraction output.
"""

from uuid import UUID

from src.core.errors import AppError


class ConnectorScriptsNotFoundError(AppError):
    """Raised when an object-class connector fix finds no generated Groovy."""

    status_code = 404
    code = "connector_scripts_not_found"

    def __init__(self, session_id: UUID, object_class: str):
        super().__init__(
            f"No generated connector code found for object class '{object_class}' in session {session_id}. "
            "Generate at least one operation for this object class first."
        )


class UnknownConnectorOperationError(AppError):
    """Raised when a caller supplies a script for an operation the connector does not have."""

    status_code = 422
    code = "unknown_connector_operation"

    def __init__(self, operation_key: str, session_id: UUID):
        super().__init__(
            f"Operation '{operation_key}' has no generated code in session {session_id}, "
            "so a replacement script cannot be applied to it."
        )


class InvalidConnectorScriptOverrideError(AppError):
    """Raised when a caller-supplied fix override is not valid Groovy."""

    status_code = 422
    code = "invalid_connector_script_override"

    def __init__(self, operation_key: str, reason: str):
        super().__init__(f"Script override for operation '{operation_key}' is invalid Groovy: {reason}")


class ConnectorFixContextTooLargeError(AppError):
    """Raised when assembled object-class scripts exceed the fix input budget."""

    status_code = 413
    code = "connector_fix_context_too_large"

    def __init__(self, *, input_tokens: int, limit: int):
        super().__init__(
            f"The object-class fix input is estimated at {input_tokens} tokens, "
            f"above the {limit}-token limit (CODEGEN__FIX_MAX_INPUT_TOKENS). "
            "Repair the failing operations individually via their own endpoints, "
            "reduce the input, or raise the limit."
        )


class ConnectorFixPassFailedError(AppError):
    """Raised when an LLM fix pass produces no valid structured response."""

    status_code = 502
    code = "connector_fix_pass_failed"

    def __init__(self):
        super().__init__(
            "The connector fix could not be completed because the language model did not return "
            "a valid structured response. See the job errors for details and try again."
        )


class ConnectorFixEscalationFailedError(AppError):
    """Raised when the documentation pass fails and the first pass has no usable repair."""

    status_code = 502
    code = "connector_fix_escalation_failed"

    def __init__(self):
        super().__init__(
            "The documentation-assisted connector fix failed and the first pass produced no valid repair to keep. "
            "See the job errors for details."
        )


class ConnectorFixProducedNoValidScriptError(AppError):
    """Raised when every script the model returned for a fix was rejected."""

    status_code = 422
    code = "connector_fix_produced_no_valid_script"

    def __init__(self, rejected_count: int):
        super().__init__(
            f"The model proposed {rejected_count} fixed script(s) and none of them could be used. "
            "See the job errors for the per-operation reason."
        )
