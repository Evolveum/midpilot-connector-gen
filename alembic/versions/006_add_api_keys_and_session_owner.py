"""Add api_keys table and sessions.api_key_id owner column

Revision ID: 006
Revises: 005
Create Date: 2026-07-27

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "006"
down_revision: Union[str, Sequence[str], None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "api_keys",
        sa.Column("api_key_id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("revoked_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("api_key_id"),
        sa.UniqueConstraint("key_hash"),
    )
    op.add_column("sessions", sa.Column("api_key_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_sessions_api_key_id",
        "sessions",
        "api_keys",
        ["api_key_id"],
        ["api_key_id"],
        ondelete="SET NULL",
    )
    op.create_index("idx_sessions_api_key_id", "sessions", ["api_key_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_sessions_api_key_id", table_name="sessions")
    op.drop_constraint("fk_sessions_api_key_id", "sessions", type_="foreignkey")
    op.drop_column("sessions", "api_key_id")
    op.drop_table("api_keys")
