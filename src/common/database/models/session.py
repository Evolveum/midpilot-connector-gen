# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from datetime import datetime
from typing import TYPE_CHECKING, List
from uuid import UUID, uuid4

from sqlalchemy import Index, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, utc_now

if TYPE_CHECKING:
    from .documentation_item import DocumentationItem
    from .job import Job
    from .relevant_chunk import RelevantChunk
    from .session_data import SessionData


class Session(Base):
    """Session table - stores basic session information."""

    __tablename__ = "sessions"

    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
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

    # Relationships
    jobs: Mapped[List["Job"]] = relationship("Job", back_populates="session", cascade="all, delete-orphan")
    documentation_items: Mapped[List["DocumentationItem"]] = relationship(
        "DocumentationItem", back_populates="session", cascade="all, delete-orphan"
    )
    session_data: Mapped[List["SessionData"]] = relationship(
        "SessionData", back_populates="session", cascade="all, delete-orphan"
    )
    relevant_chunks: Mapped[List["RelevantChunk"]] = relationship(
        "RelevantChunk", back_populates="session", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_sessions_created_at", "created_at"),
        Index("idx_sessions_updated_at", "updated_at"),
    )
