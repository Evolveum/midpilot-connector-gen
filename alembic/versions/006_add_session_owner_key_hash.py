"""Add gateway API-key fingerprints to sessions.

Revision ID: 006
Revises: 005

Pre-production schema replacement: databases initialized with the former
api_keys table require the documented schema cleanup. No key backfill is retained.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "006"
down_revision: Union[str, Sequence[str], None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("owner_key_hash", sa.String(64), nullable=True))
    op.create_index("idx_sessions_owner_key_hash", "sessions", ["owner_key_hash"], unique=False)
    op.create_check_constraint("check_session_owner_key_hash_hex", "sessions", "owner_key_hash ~ '^[0-9a-f]{64}$'")


def downgrade() -> None:
    op.drop_constraint("check_session_owner_key_hash_hex", "sessions", type_="check")
    op.drop_index("idx_sessions_owner_key_hash", table_name="sessions")
    op.drop_column("sessions", "owner_key_hash")
