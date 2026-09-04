# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List
from uuid import UUID, uuid4

from sqlalchemy import ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.models.base import Base
from src.shared.clock import utc_now

if TYPE_CHECKING:
    from src.database.models.document import Document


class DocumentationChunk(Base):
    """One retrievable piece of a document, as stored for context assembly."""

    __tablename__ = "documentation_chunks"

    chunk_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
        comment="Unique identifier for the documentation chunk",
    )
    # Denormalized from the owning document so session-scoped reads and the
    # relevant_chunks reference need no join; the composite foreign key below
    # forbids it from naming a different session than the document does.
    session_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    doc_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)

    # Position of this chunk inside its document, as produced by chunking.
    chunk_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    scrape_job_ids: Mapped[List[str]] = mapped_column(
        "scrape_job_ids",
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        comment=(
            "Scrape job IDs that created or needed this chunk. Per chunk, not per document: "
            "a later job can link a subset of an existing document's chunks. "
            "WARNING: ids are stored as strings in JSONB for easier querying"
        ),
    )
    origin_job_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    origin_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Content
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Chunk-specific metadata only; whatever describes the whole document lives
    # on ``documents``. Named doc_metadata to avoid the SQLAlchemy reserved name.
    doc_metadata: Mapped[Dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=text("NOW()"),
    )

    # Relationships
    document: Mapped["Document"] = relationship("Document", back_populates="chunks")

    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "doc_id"],
            ["documents.session_id", "documents.doc_id"],
            name="fk_documentation_chunks_document",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["origin_job_id"],
            ["jobs.job_id"],
            name="fk_documentation_chunks_origin_job",
            ondelete="SET NULL",
        ),
        UniqueConstraint(
            "session_id",
            "origin_job_id",
            "origin_key",
            name="uq_doc_chunks_job_origin",
        ),
        # Backs the composite foreign key from relevant_chunks.
        UniqueConstraint(
            "session_id",
            "chunk_id",
            "doc_id",
            name="uq_doc_chunks_session_chunk_doc",
        ),
        # Referencing side of the document foreign key; its leading column also
        # serves the session-scoped reads.
        Index("idx_doc_chunks_session_doc", "session_id", "doc_id"),
        Index("idx_doc_chunks_origin_job_id", "origin_job_id"),
        # Containment index for the `scrape_job_ids @> [...]` lookups that resolve
        # which chunks one scrape job is responsible for.
        Index(
            "idx_doc_chunks_scrape_job_ids_gin",
            "scrape_job_ids",
            postgresql_using="gin",
            postgresql_ops={"scrape_job_ids": "jsonb_path_ops"},
        ),
    )
