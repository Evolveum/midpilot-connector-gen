"""Rework relevant_chunks for result_key + entity_key + sequence payload

Revision ID: 005
Revises: 004
Create Date: 2026-05-13 15:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "005"
down_revision: Union[str, Sequence[str], None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_table("relevant_chunks")

    op.create_table(
        "relevant_chunks",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("doc_id", sa.UUID(), nullable=False),
        sa.Column("chunk_id", sa.UUID(), nullable=False),
        sa.Column(
            "relevant_sequence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("result_key", sa.String(length=255), nullable=False),
        sa.Column("entity_key", sa.String(length=255), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["documentation_items.chunk_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id",
            "result_key",
            "entity_key",
            "chunk_id",
            "relevant_sequence",
            name="uq_relevant_chunk_unique",
        ),
    )
    op.create_index("idx_relevant_chunks_session_id", "relevant_chunks", ["session_id"], unique=False)
    op.create_index("idx_relevant_chunks_result_key", "relevant_chunks", ["result_key"], unique=False)
    op.create_index("idx_relevant_chunks_doc_id", "relevant_chunks", ["doc_id"], unique=False)
    op.create_index("idx_relevant_chunks_chunk_id", "relevant_chunks", ["chunk_id"], unique=False)
    op.create_index("idx_relevant_chunks_entity_key", "relevant_chunks", ["entity_key"], unique=False)
    op.create_index(
        "idx_relevant_chunks_session_result",
        "relevant_chunks",
        ["session_id", "result_key"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("relevant_chunks")

    op.create_table(
        "relevant_chunks",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("entity_type", sa.String(length=255), nullable=False),
        sa.Column("doc_id", sa.UUID(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["doc_id"], ["documentation_items.chunk_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "entity_type", "doc_id", name="uq_relevant_chunk_unique"),
    )
    op.create_index("idx_relevant_chunks_doc_id", "relevant_chunks", ["doc_id"], unique=False)
    op.create_index("idx_relevant_chunks_entity_type", "relevant_chunks", ["entity_type"], unique=False)
    op.create_index("idx_relevant_chunks_session_id", "relevant_chunks", ["session_id"], unique=False)
    op.create_index(
        "idx_relevant_chunks_session_entity", "relevant_chunks", ["session_id", "entity_type"], unique=False
    )
    op.create_index("ix_relevant_chunks_doc_id", "relevant_chunks", ["doc_id"], unique=False)
    op.create_index("ix_relevant_chunks_entity_type", "relevant_chunks", ["entity_type"], unique=False)
    op.create_index("ix_relevant_chunks_session_id", "relevant_chunks", ["session_id"], unique=False)
