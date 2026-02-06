# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, utc_now

if TYPE_CHECKING:
    from .session import Session


class DocumentationItem(Base):
    """Documentation items table - stores scraped or uploaded documentation chunks."""

    __tablename__ = "documentation_items"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
        comment="Unique identifier for the documentation chunk",
    )
    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    page_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True, index=True)

    source: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Content
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Metadata stored as JSONB (using doc_metadata to avoid SQLAlchemy reserved name)
    doc_metadata: Mapped[Dict[str, Any]] = mapped_column(
        "metadata",  # Column name in DB
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=text("NOW()"),
        index=True,
    )

    # Relationships
    session: Mapped["Session"] = relationship("Session", back_populates="documentation_items")

    __table_args__ = (
        CheckConstraint("source IN ('scraper', 'upload')", name="check_doc_source"),
        Index("idx_doc_items_session_id", "session_id"),
        Index("idx_doc_items_page_id", "page_id"),
        Index("idx_doc_items_source", "source"),
        Index("idx_doc_items_created_at", "created_at"),
        Index("idx_doc_items_metadata_gin", "metadata", postgresql_using="gin"),  # GIN index for JSONB queries
    )
