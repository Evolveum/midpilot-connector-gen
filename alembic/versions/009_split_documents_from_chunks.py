# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Split documentation_items into documents and documentation_chunks

Every chunk repeated what is true of the whole document it came from: its
``source``, its ``url``, and the ``content_type`` and ``filename`` carried in the
metadata JSONB. A 64-chunk upload stored those 64 times, and nothing stopped two
chunks of one document from disagreeing about them.

``documents`` now holds the document-level fields, keyed by ``(session_id,
doc_id)`` - a document id travels with an exported bundle, so the same id can
legitimately exist in two sessions. ``documentation_chunks`` keeps what differs
per chunk and references its document through a composite foreign key, which is
also what keeps its denormalized ``session_id`` equal to the document's.

Two details differ from a naive split, both decided by measuring the data:

* ``scrape_job_ids`` stays on the chunk. It is not a document-level fact: a later
  job can link a subset of an existing document's chunks, and it does - the value
  varied within 16 of 35 documents.
* ``chunk_number`` becomes a real column instead of a metadata key, since it is
  what orders a document's chunks.

The API payload is unchanged: ``DocumentationRepository`` merges the
document-level fields back into each chunk's ``metadata`` on read.

Chunks that never had a document identity (``doc_id IS NULL``) become
single-chunk documents; there is no other value to give them.

Revision ID: 009
Revises: 008
Create Date: 2026-08-03
"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

revision: str = "009"
down_revision: Union[str, Sequence[str], None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_OLD_SCRAPE_JOB_IDS_COMMENT = (
    "List of scrape job IDs that created or needed this documentation item, "
    "WARNING: ids are stored as strings in JSONB for easier querying"
)
_NEW_SCRAPE_JOB_IDS_COMMENT = (
    "Scrape job IDs that created or needed this chunk. Per chunk, not per document: "
    "a later job can link a subset of an existing document's chunks. "
    "WARNING: ids are stored as strings in JSONB for easier querying"
)

_ADOPT_ORPHAN_CHUNKS = sa.text("UPDATE documentation_items SET doc_id = gen_random_uuid() WHERE doc_id IS NULL")

# The document-level fields are identical across a document's chunks, so any
# aggregate picks the same value; min() is used because it is defined for text.
_FILL_DOCUMENTS = sa.text(
    """
    INSERT INTO documents (session_id, doc_id, source, url, filename, content_type, created_at)
    SELECT session_id,
           doc_id,
           min(source),
           min(url),
           min(metadata ->> 'filename'),
           min(metadata ->> 'content_type'),
           min(created_at)
    FROM documentation_items
    GROUP BY session_id, doc_id
    """
)

_RENAMED_CONSTRAINTS: tuple[tuple[str, str, str], ...] = (
    ("documentation_chunks", "documentation_items_pkey", "documentation_chunks_pkey"),
    ("documentation_chunks", "uq_doc_items_job_origin", "uq_doc_chunks_job_origin"),
    ("documentation_chunks", "uq_doc_items_session_chunk_doc", "uq_doc_chunks_session_chunk_doc"),
    ("documentation_chunks", "fk_documentation_items_origin_job_id", "fk_documentation_chunks_origin_job"),
    ("relevant_chunks", "fk_relevant_chunks_documentation_item", "fk_relevant_chunks_documentation_chunk"),
)

_RENAMED_INDEXES: tuple[tuple[str, str], ...] = (
    ("idx_doc_items_origin_job_id", "idx_doc_chunks_origin_job_id"),
    ("idx_doc_items_scrape_job_ids_gin", "idx_doc_chunks_scrape_job_ids_gin"),
)

# Superseded by idx_doc_chunks_session_doc, or by a column that moved to documents.
_DROPPED_CHUNK_INDEXES: tuple[tuple[str, list[str]], ...] = (
    ("idx_doc_items_session_id", ["session_id"]),
    ("idx_doc_items_doc_id", ["doc_id"]),
    ("idx_doc_items_source", ["source"]),
    ("idx_doc_items_created_at", ["created_at"]),
)


def _adopt_orphan_chunks() -> None:
    """Give every chunk a document id.

    Offline (``--sql``) generation writes the statement to a script instead of
    executing it, so there is no row count to report.
    """
    if context.is_offline_mode():
        op.execute(_ADOPT_ORPHAN_CHUNKS)
        return

    adopted = op.get_bind().execute(_ADOPT_ORPHAN_CHUNKS).rowcount
    if adopted:
        logger.warning("Documentation split: %s chunk(s) had no document and became single-chunk documents", adopted)


def upgrade() -> None:
    _adopt_orphan_chunks()

    op.create_table(
        "documents",
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("doc_id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("filename", sa.Text(), nullable=True),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint("source IN ('scraper', 'upload')", name="check_document_source"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id", "doc_id"),
    )
    op.create_index("idx_documents_session_created", "documents", ["session_id", "created_at"], unique=False)
    op.execute(_FILL_DOCUMENTS)

    op.rename_table("documentation_items", "documentation_chunks")
    for table_name, old_name, new_name in _RENAMED_CONSTRAINTS:
        op.execute(f"ALTER TABLE {table_name} RENAME CONSTRAINT {old_name} TO {new_name}")
    for old_name, new_name in _RENAMED_INDEXES:
        op.execute(f"ALTER INDEX {old_name} RENAME TO {new_name}")
    for index_name, _columns in _DROPPED_CHUNK_INDEXES:
        op.drop_index(index_name, table_name="documentation_chunks")

    op.add_column("documentation_chunks", sa.Column("chunk_number", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE documentation_chunks
        SET chunk_number = (metadata ->> 'chunk_number')::integer
        WHERE metadata ->> 'chunk_number' ~ '^-?[0-9]+$'
        """
    )
    op.execute("UPDATE documentation_chunks SET metadata = metadata - 'chunk_number' - 'content_type' - 'filename'")

    op.alter_column("documentation_chunks", "doc_id", existing_type=sa.UUID(), nullable=False)
    op.drop_constraint("check_doc_source", "documentation_chunks", type_="check")
    op.drop_constraint("documentation_items_session_id_fkey", "documentation_chunks", type_="foreignkey")
    op.drop_column("documentation_chunks", "source")
    op.drop_column("documentation_chunks", "url")

    op.create_foreign_key(
        "fk_documentation_chunks_document",
        "documentation_chunks",
        "documents",
        ["session_id", "doc_id"],
        ["session_id", "doc_id"],
        ondelete="CASCADE",
    )
    op.create_index("idx_doc_chunks_session_doc", "documentation_chunks", ["session_id", "doc_id"], unique=False)
    op.alter_column(
        "documentation_chunks",
        "scrape_job_ids",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        existing_nullable=False,
        existing_server_default=sa.text("'[]'::jsonb"),
        comment=_NEW_SCRAPE_JOB_IDS_COMMENT,
        existing_comment=_OLD_SCRAPE_JOB_IDS_COMMENT,
    )


def downgrade() -> None:
    op.alter_column(
        "documentation_chunks",
        "scrape_job_ids",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        existing_nullable=False,
        existing_server_default=sa.text("'[]'::jsonb"),
        comment=_OLD_SCRAPE_JOB_IDS_COMMENT,
        existing_comment=_NEW_SCRAPE_JOB_IDS_COMMENT,
    )
    op.drop_index("idx_doc_chunks_session_doc", table_name="documentation_chunks")
    op.drop_constraint("fk_documentation_chunks_document", "documentation_chunks", type_="foreignkey")

    op.add_column("documentation_chunks", sa.Column("url", sa.Text(), nullable=True))
    op.add_column("documentation_chunks", sa.Column("source", sa.String(length=20), nullable=True))
    op.execute(
        """
        UPDATE documentation_chunks AS c
        SET source = d.source,
            url = d.url,
            metadata = c.metadata
                || CASE WHEN c.chunk_number IS NULL THEN '{}'::jsonb
                        ELSE jsonb_build_object('chunk_number', c.chunk_number) END
                || CASE WHEN d.content_type IS NULL THEN '{}'::jsonb
                        ELSE jsonb_build_object('content_type', d.content_type) END
                || CASE WHEN d.filename IS NULL THEN '{}'::jsonb
                        ELSE jsonb_build_object('filename', d.filename) END
        FROM documents AS d
        WHERE d.session_id = c.session_id AND d.doc_id = c.doc_id
        """
    )
    op.alter_column("documentation_chunks", "source", existing_type=sa.String(length=20), nullable=False)
    op.create_check_constraint("check_doc_source", "documentation_chunks", "source IN ('scraper', 'upload')")
    op.create_foreign_key(
        "documentation_items_session_id_fkey",
        "documentation_chunks",
        "sessions",
        ["session_id"],
        ["session_id"],
        ondelete="CASCADE",
    )
    op.alter_column("documentation_chunks", "doc_id", existing_type=sa.UUID(), nullable=True)
    op.drop_column("documentation_chunks", "chunk_number")

    for index_name, columns in _DROPPED_CHUNK_INDEXES:
        op.create_index(index_name, "documentation_chunks", columns, unique=False)
    for old_name, new_name in _RENAMED_INDEXES:
        op.execute(f"ALTER INDEX {new_name} RENAME TO {old_name}")
    for table_name, old_name, new_name in _RENAMED_CONSTRAINTS:
        op.execute(f"ALTER TABLE {table_name} RENAME CONSTRAINT {new_name} TO {old_name}")
    op.rename_table("documentation_chunks", "documentation_items")

    op.drop_index("idx_documents_session_created", table_name="documents")
    op.drop_table("documents")
