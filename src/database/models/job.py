# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List
from uuid import UUID, uuid4

from sqlalchemy import ARRAY, CheckConstraint, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.models.base import Base, utc_now

if TYPE_CHECKING:
    from src.database.models.job_artifact import JobArtifact
    from src.database.models.job_progress import JobProgress
    from src.database.models.session import Session


class Job(Base):
    """Job table - stores job information and execution state."""

    __tablename__ = "jobs"

    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=text("NOW()"),
        onupdate=utc_now,
    )
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    input: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    normalized_input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[Dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    execution_payload: Mapped[Dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    errors: Mapped[List[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    execution_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    available_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=text("NOW()"),
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default=text("3"))
    documentation_wait_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    # Relationships
    session: Mapped["Session"] = relationship("Session", back_populates="jobs")
    progress: Mapped["JobProgress | None"] = relationship(
        "JobProgress", back_populates="job", cascade="all, delete-orphan", uselist=False
    )
    artifacts: Mapped[list["JobArtifact"]] = relationship(
        "JobArtifact",
        back_populates="job",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'finished', 'failed')", name="check_job_status"),
        Index("idx_jobs_session_id", "session_id"),
        Index("idx_jobs_created_at", "created_at"),
        Index("idx_jobs_status_type_created", "status", "job_type", "created_at"),
        Index(
            "idx_jobs_reuse_lookup",
            "job_type",
            "normalized_input_hash",
            "created_at",
            postgresql_where=text("status = 'finished'"),
        ),
        Index(
            "idx_jobs_claimable_queued",
            "available_at",
            "created_at",
            "job_id",
            postgresql_where=text("status = 'queued'"),
        ),
        Index(
            "idx_jobs_claimable_expired",
            "claim_expires_at",
            postgresql_where=text("status = 'running'"),
        ),
        CheckConstraint("attempt_count >= 0", name="check_job_attempt_count"),
        CheckConstraint("max_attempts > 0", name="check_job_max_attempts"),
        CheckConstraint("attempt_count <= max_attempts", name="check_job_attempt_bounds"),
        CheckConstraint(
            "started_at IS NULL OR finished_at IS NULL OR finished_at >= started_at",
            name="check_job_timeline",
        ),
    )
