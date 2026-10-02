# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.models.base import Base
from src.shared.clock import utc_now

if TYPE_CHECKING:
    from src.database.models.job import Job


class JobProgress(Base):
    """Job progress table - one current progress snapshot per job."""

    __tablename__ = "job_progress"

    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("jobs.job_id", ondelete="CASCADE"),
        primary_key=True,
    )

    stage: Mapped[str | None] = mapped_column(String(50), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    total_processing: Mapped[int | None] = mapped_column(nullable=True)
    processing_completed: Mapped[int | None] = mapped_column(nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=text("NOW()"),
        onupdate=utc_now,
    )

    job: Mapped["Job"] = relationship("Job", back_populates="progress", uselist=False)

    __table_args__ = (
        CheckConstraint("total_processing IS NULL OR total_processing >= 0", name="check_progress_total"),
        CheckConstraint(
            "processing_completed IS NULL OR processing_completed >= 0",
            name="check_progress_completed",
        ),
    )
