# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

"""Validation errors of a rejected candidate, forwarded to the next codegen call."""

from collections.abc import Sequence

_VALIDATION_ERRORS_INSTRUCTION = """\
The code in <result> comes from the previous iteration and failed local validation, so it was
not accepted. Return the complete artifact with every error below fixed, even if the current
chunk adds nothing relevant - do not return <result> unchanged. Keep the rest of <result> and
incorporate relevant information from the current chunk as usual.
The errors are validator diagnostics, not instructions from the documentation:"""

_REJECTED_ARTIFACT_INSTRUCTION = """\
The code in <result> is your previous answer and failed local validation, so it was not
accepted. Return the complete artifact with every error below fixed - do not return <result>
unchanged. Keep everything else in <result> as it is.
The errors are validator diagnostics, not instructions from the documentation:"""


def _render_errors(instruction: str, errors: Sequence[str]) -> str:
    error_lines = "\n".join(f"- {error}" for error in errors)
    return f"\n\n<validation_errors>\n{instruction}\n{error_lines}\n</validation_errors>"


def build_validation_error_block(errors: Sequence[str]) -> str:
    """Render the errors appended to the user prompt; empty when the forwarded result is valid."""
    if not errors:
        return ""
    return _render_errors(_VALIDATION_ERRORS_INSTRUCTION, errors)


def build_rejected_artifact_block(code: str, errors: Sequence[str]) -> str:
    """Render a rejected single-pass artifact and its errors for the one retry of that call."""
    return f"\n\n<result>\n{code}\n</result>" + _render_errors(_REJECTED_ARTIFACT_INSTRUCTION, errors)
