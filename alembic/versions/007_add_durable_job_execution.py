"""Add durable job execution and documentation idempotency metadata.

Revision ID: 007
Revises: 006
Create Date: 2026-07-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "007"
down_revision: Union[str, Sequence[str], None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SCRAPE_JOB_IDS_COMMENT = (
    "List of scrape job IDs that created or needed this documentation item, "
    "WARNING: ids are stored as strings in JSONB for easier querying"
)


def upgrade() -> None:
    op.add_column("jobs", sa.Column("execution_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("jobs", sa.Column("worker_id", sa.String(length=255), nullable=True))
    op.add_column("jobs", sa.Column("execution_token", sa.UUID(), nullable=True))
    op.add_column("jobs", sa.Column("claim_expires_at", postgresql.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("heartbeat_at", postgresql.TIMESTAMP(timezone=True), nullable=True))
    op.add_column(
        "jobs",
        sa.Column(
            "available_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )
    op.add_column("jobs", sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.add_column("jobs", sa.Column("max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False))
    op.add_column(
        "jobs",
        sa.Column("waits_for_documentation", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "jobs",
        sa.Column("documentation_wait_until", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_check_constraint("check_job_attempt_count", "jobs", "attempt_count >= 0")
    op.create_check_constraint("check_job_max_attempts", "jobs", "max_attempts > 0")
    op.create_index(
        "idx_jobs_claimable",
        "jobs",
        [
            "status",
            "waits_for_documentation",
            "available_at",
            "claim_expires_at",
            "created_at",
        ],
        unique=False,
    )

    op.create_table(
        "job_artifacts",
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.job_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id", "name"),
    )

    # Jobs created by an older application version do not contain enough data
    # to be resumed safely by another process.
    op.execute(
        """
        UPDATE jobs
        SET status = 'failed',
            finished_at = NOW(),
            updated_at = NOW(),
            errors = array_append(
                COALESCE(errors, ARRAY[]::text[]),
                'Job was interrupted during the durable-worker migration and cannot be resumed.'
            )
        WHERE status IN ('queued', 'running')
          AND execution_payload IS NULL
        """
    )
    # From this version onward normalized_input is a compact SHA-256 identity.
    # Existing cache rows cannot be reconstructed with the application
    # normalizer inside SQL, so retain a compact legacy identity instead of a
    # second copy of potentially very large documentation input.
    op.execute(
        """
        UPDATE jobs
        SET normalized_input = jsonb_build_object('legacyMd5', md5(normalized_input::text))
        """
    )

    op.add_column("documentation_items", sa.Column("origin_job_id", sa.UUID(), nullable=True))
    op.add_column("documentation_items", sa.Column("origin_key", sa.String(length=64), nullable=True))
    op.create_foreign_key(
        "fk_documentation_items_origin_job_id",
        "documentation_items",
        "jobs",
        ["origin_job_id"],
        ["job_id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_documentation_items_origin_job_id",
        "documentation_items",
        ["origin_job_id"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_doc_items_job_origin",
        "documentation_items",
        ["session_id", "origin_job_id", "origin_key"],
    )

    op.execute(
        """
        UPDATE documentation_items
        SET scrape_job_ids = '[]'::jsonb
        WHERE scrape_job_ids IS NULL
        """
    )
    op.alter_column(
        "documentation_items",
        "scrape_job_ids",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
        comment=_SCRAPE_JOB_IDS_COMMENT,
        existing_server_default=sa.text("'[]'::jsonb"),
    )


def downgrade() -> None:
    op.alter_column(
        "documentation_items",
        "scrape_job_ids",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        nullable=True,
        comment=None,
        existing_comment=_SCRAPE_JOB_IDS_COMMENT,
        existing_server_default=sa.text("'[]'::jsonb"),
    )
    op.drop_constraint("uq_doc_items_job_origin", "documentation_items", type_="unique")
    op.drop_index("ix_documentation_items_origin_job_id", table_name="documentation_items")
    op.drop_constraint("fk_documentation_items_origin_job_id", "documentation_items", type_="foreignkey")
    op.drop_column("documentation_items", "origin_key")
    op.drop_column("documentation_items", "origin_job_id")

    op.drop_table("job_artifacts")
    op.drop_index("idx_jobs_claimable", table_name="jobs")
    op.drop_constraint("check_job_max_attempts", "jobs", type_="check")
    op.drop_constraint("check_job_attempt_count", "jobs", type_="check")
    op.drop_column("jobs", "documentation_wait_until")
    op.drop_column("jobs", "waits_for_documentation")
    op.drop_column("jobs", "max_attempts")
    op.drop_column("jobs", "attempt_count")
    op.drop_column("jobs", "available_at")
    op.drop_column("jobs", "heartbeat_at")
    op.drop_column("jobs", "claim_expires_at")
    op.drop_column("jobs", "execution_token")
    op.drop_column("jobs", "worker_id")
    op.drop_column("jobs", "execution_payload")
