# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.models.base import Base
from src.database.models.documentation_chunk import DocumentationChunk
from src.database.models.session import Session
from src.shared.clock import utc_now


class RelevantChunk(Base):
    """Relevant chunks table - tracks which documentation chunks are relevant for specific extraction outputs."""

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
    )
    doc_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    chunk_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    relevant_sequence: Mapped[dict[str, str]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    result_key: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=text("NOW()"),
    )

    session: Mapped["Session"] = relationship("Session", back_populates="relevant_chunks")
    chunk: Mapped["DocumentationChunk"] = relationship("DocumentationChunk", viewonly=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "chunk_id", "doc_id"],
            [
                "documentation_chunks.session_id",
                "documentation_chunks.chunk_id",
                "documentation_chunks.doc_id",
            ],
            name="fk_relevant_chunks_documentation_chunk",
            ondelete="CASCADE",
        ),
        Index(
            "uq_relevant_chunk_unique",
            "session_id",
            "result_key",
            "entity_key",
            "chunk_id",
            text("md5(relevant_sequence::text)"),
            unique=True,
        ),
        Index("idx_relevant_chunks_chunk_ref", "chunk_id", "session_id", "doc_id"),
    )
