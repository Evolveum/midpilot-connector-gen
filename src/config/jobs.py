# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from pydantic import BaseModel, Field, model_validator


class JobsSettings(BaseModel):
    """Database-backed background worker configuration.

    The settings are process-local. In particular, ``max_concurrent_jobs`` is
    applied by every FastAPI worker process.
    """

    enabled: bool = True
    max_concurrent_jobs: int = Field(default=2, ge=1)
    poll_interval_seconds: float = Field(default=0.5, gt=0)
    reaper_interval_seconds: float = Field(default=30.0, gt=0)
    claim_timeout_seconds: float = Field(default=300.0, gt=0)
    heartbeat_interval_seconds: float = Field(default=30.0, gt=0)
    max_attempts: int = Field(default=3, ge=1)
    shutdown_grace_seconds: float = Field(default=10.0, ge=0)
    claim_release_timeout_seconds: float = Field(default=3.0, gt=0)
    documentation_write_batch_size: int = Field(default=20, ge=1)
    cpu_processes: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_claim_timing(self) -> "JobsSettings":
        if self.heartbeat_interval_seconds >= self.claim_timeout_seconds:
            raise ValueError("JOBS__HEARTBEAT_INTERVAL_SECONDS must be lower than JOBS__CLAIM_TIMEOUT_SECONDS")
        return self
