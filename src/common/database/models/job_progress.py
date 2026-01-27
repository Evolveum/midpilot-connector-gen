# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, utc_now

if TYPE_CHECKING:
    from .job import Job


class JobProgress(Base):
    """Job progress table - tracks detailed progress information for jobs."""

    __tablename__ = "job_progress"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("jobs.job_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    stage: Mapped[str | None] = mapped_column(String(50), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Progress tracking
    total_processing: Mapped[int | None] = mapped_column(nullable=True)
    processing_completed: Mapped[int | None] = mapped_column(nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=text("NOW()"),
        onupdate=utc_now,
    )

    # Relationships (1:1 with Job)
    job: Mapped["Job"] = relationship("Job", back_populates="progress", uselist=False)
