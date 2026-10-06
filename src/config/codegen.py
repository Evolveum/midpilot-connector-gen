# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from pydantic import BaseModel, Field


class CodegenSettings(BaseModel):
    """
    Settings for connector code generation.

    Model selection and transport retries use the shared ``LLM__*`` settings.

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
