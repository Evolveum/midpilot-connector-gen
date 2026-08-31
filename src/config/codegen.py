# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from pydantic import BaseModel, Field


class CodegenSettings(BaseModel):
    """
    Settings for Groovy code generation.

    Per-operation generation is driven entirely by the shared ``LLM__*`` settings.
    This bound exists for the object-class fix, whose input size grows with all
    generated operations of one object class rather than with a single operation.

    :param fix_max_input_tokens: Maximum estimated input tokens sent to one
        object-class fix LLM pass. Inputs above this are rejected rather than
        truncated: a partially seen object class produces confidently wrong
        cross-operation fixes.
    """

    fix_max_input_tokens: int = Field(
        120000,
        ge=100,
        description=(
            "Maximum estimated input tokens sent to one object-class fix LLM pass. "
            "Larger inputs are rejected, not truncated."
        ),
    )
