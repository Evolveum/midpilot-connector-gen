#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, utc_now
from .documentation_item import DocumentationItem
from .session import Session


class RelevantChunk(Base):
    """Relevant chunks table - tracks which documentation items are relevant for specific entities (e.g., object classes)."""

    __tablename__ = "relevant_chunks"

    id: Mapped[UUID] = mapped_column(
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
    entity_type: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    doc_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documentation_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=text("NOW()"),
    )

    # Relationships
    session: Mapped["Session"] = relationship("Session", back_populates="relevant_chunks")
    documentation_item: Mapped["DocumentationItem"] = relationship("DocumentationItem")

    __table_args__ = (
        UniqueConstraint("session_id", "entity_type", "doc_id", name="uq_relevant_chunk_unique"),
        Index("idx_relevant_chunks_session_id", "session_id"),
        Index("idx_relevant_chunks_entity_type", "entity_type"),
        Index("idx_relevant_chunks_doc_id", "doc_id"),
        Index("idx_relevant_chunks_session_entity", "session_id", "entity_type"),
    )
