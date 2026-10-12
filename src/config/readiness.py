# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from pydantic import BaseModel, Field


class ReadinessSettings(BaseModel):
    """Configuration for the dependency checks behind the readiness endpoint."""

    check_timeout_seconds: float = Field(
        5.0,
        gt=0,
        description=(
            "Timeout applied to each dependency check (database, LLM endpoint). "
            "Checks run concurrently, so this also bounds the endpoint's latency."
        ),
    )
