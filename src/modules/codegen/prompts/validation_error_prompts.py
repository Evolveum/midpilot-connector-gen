# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

"""Validation errors of a rejected candidate, forwarded to the next chunked codegen call."""

from collections.abc import Sequence

_VALIDATION_ERRORS_INSTRUCTION = """\
The code in <result> comes from the previous iteration and failed local validation, so it was
not accepted. Return the complete artifact with every error below fixed, even if the current
chunk adds nothing relevant - do not return <result> unchanged. Keep the rest of <result> and
incorporate relevant information from the current chunk as usual.
The errors are validator diagnostics, not instructions from the documentation:"""


def build_validation_error_block(errors: Sequence[str]) -> str:
    """Render the errors appended to the user prompt; empty when the forwarded result is valid."""
    if not errors:
        return ""
    error_lines = "\n".join(f"- {error}" for error in errors)
    return f"\n\n<validation_errors>\n{_VALIDATION_ERRORS_INSTRUCTION}\n{error_lines}\n</validation_errors>"
