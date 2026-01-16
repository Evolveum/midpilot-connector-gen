"""
Job model - stores job information and execution state.
"""

#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List
from uuid import UUID, uuid4

from sqlalchemy import ARRAY, CheckConstraint, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, utc_now

if TYPE_CHECKING:
    from .job_progress import JobProgress
    from .session import Session


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
        index=True,
    )
    job_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=text("NOW()"),
        index=True,
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

    # JSONB columns for flexible data storage
    input: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result: Mapped[Dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Array of error messages
    errors: Mapped[List[str] | None] = mapped_column(ARRAY(Text), nullable=True)

    # Relationships
    session: Mapped["Session"] = relationship("Session", back_populates="jobs")
    progress: Mapped["JobProgress | None"] = relationship(
        "JobProgress", back_populates="job", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'finished', 'failed')", name="check_job_status"),
        Index("idx_jobs_session_id", "session_id"),
        Index("idx_jobs_status", "status"),
        Index("idx_jobs_type", "job_type"),
        Index("idx_jobs_status_type_created", "status", "job_type", "created_at"),  # For claim_next_job
    )
