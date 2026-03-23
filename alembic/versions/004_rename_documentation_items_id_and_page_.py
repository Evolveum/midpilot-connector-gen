"""Rename documentation_items id/page_id columns to chunk_id/doc_id

Revision ID: 004
Revises: 003
Create Date: 2026-03-19 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, Sequence[str], None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index("idx_doc_items_page_id", table_name="documentation_items")
    op.drop_index("ix_documentation_items_page_id", table_name="documentation_items")

    op.alter_column("documentation_items", "id", new_column_name="chunk_id")
    op.alter_column("documentation_items", "page_id", new_column_name="doc_id")

    op.create_index("idx_doc_items_doc_id", "documentation_items", ["doc_id"], unique=False)
    op.create_index("ix_documentation_items_doc_id", "documentation_items", ["doc_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_documentation_items_doc_id", table_name="documentation_items")
    op.drop_index("idx_doc_items_doc_id", table_name="documentation_items")

    op.alter_column("documentation_items", "doc_id", new_column_name="page_id")
    op.alter_column("documentation_items", "chunk_id", new_column_name="id")

    op.create_index("idx_doc_items_page_id", "documentation_items", ["page_id"], unique=False)
    op.create_index("ix_documentation_items_page_id", "documentation_items", ["page_id"], unique=False)
