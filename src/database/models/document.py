# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from datetime import datetime
from typing import TYPE_CHECKING, List
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.models.base import Base, utc_now

if TYPE_CHECKING:
    from src.database.models.documentation_chunk import DocumentationChunk
    from src.database.models.session import Session


class Document(Base):
    """One logical documentation source: a scraped page or an uploaded file.

    Holds what is true for the document as a whole. Everything that differs
    between its parts lives on ``documentation_chunks``, so a 60-chunk upload
    stores its URL, source and content type once instead of sixty times.
    """

    __tablename__ = "documents"

    # (session_id, doc_id) is the key: a document id travels with an exported
    # bundle, so the same id can legitimately be imported into two sessions.
    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        primary_key=True,
    )
    doc_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Stored as supplied, not normalized: the value is echoed back to API
    # consumers, and the few queries over it normalize the same way
    # ``normalize_content_type`` does.
    content_type: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=text("NOW()"),
    )

    # Relationships
    session: Mapped["Session"] = relationship("Session", back_populates="documents")
    chunks: Mapped[List["DocumentationChunk"]] = relationship(
        "DocumentationChunk",
        back_populates="document",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("source IN ('scraper', 'upload')", name="check_document_source"),
        Index("idx_documents_session_created", "session_id", "created_at"),
    )
