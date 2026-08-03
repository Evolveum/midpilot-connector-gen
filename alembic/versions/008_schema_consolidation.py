# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Consolidate the schema: indexes, natural keys, and state/reference integrity

One migration, five groups of changes:

1. **Indexes.** Every column that carried both ``index=True`` on the model and an
   explicit ``Index(...)`` had two identical btree indexes; the duplicate never
   sped a read up but was maintained on every write. Indexes whose columns are a
   leading prefix of a unique constraint, or that no query filters on, go too.
   The wide ``idx_jobs_claimable`` is replaced by one partial index per branch of
   the claim predicate, the ``scrape_job_ids @> [...]`` containment lookups get
   the GIN index they were missing, and the job-reuse cache probe gets an index
   of its own. ``idx_doc_items_metadata_gin`` is dropped: the only query over
   ``metadata`` uses a functional expression a GIN index cannot serve, and
   ``pg_stat_user_indexes`` reports zero scans for it.

2. **Redundant job columns.** ``normalized_input`` was a JSONB wrapping a single
   fixed-width digest, ``waits_for_documentation`` duplicated what the wait
   deadline already says, and ``heartbeat_at`` was derivable from
   ``claim_expires_at`` while being written on every heartbeat.

3. **Natural keys.** ``job_progress`` and ``session_data`` carried a surrogate
   ``id`` next to the unique key that is their real identity.

4. **State constraints.** Invariants that were only maintained in the repository
   layer become invariants the database keeps.

5. **Reference integrity.** ``relevant_chunks`` stored ``session_id``, ``doc_id``
   and ``chunk_id`` but only ``chunk_id`` was a foreign key, so a row could claim
   a session or document the referenced chunk does not belong to - and the rows
   are built from LLM output, which is exactly where such a mismatch comes from.
   ``sessions.api_key_id`` moves from SET NULL to RESTRICT, because keys are
   revoked rather than deleted and a delete would silently orphan owned sessions.

``uq_relevant_chunk_unique`` becomes a unique index over the *digest* of
``relevant_sequence`` instead of the value itself. That column holds LLM-authored
anchor text of unbounded length, and a btree index tuple is capped at 2704 bytes,
so a long enough anchor aborted the whole extraction write. Note that the
downgrade can only succeed while every stored sequence still fits the old limit;
with oversized rows present it fails (transactionally) on recreating the plain
constraint, which is the honest outcome - the old index cannot hold that data.

Rows that already contradict the stored documentation are repaired before the
composite foreign key is added: a wrong ``doc_id`` is corrected from
``documentation_items``, and rows whose chunk does not exist in the claimed
session (or whose chunk has no document id) are deleted, because there is no
correct value to repair them to. The counts are logged. That step is not
reversible - the downgrade restores the weaker foreign key but cannot bring
deleted rows back.

If the upgrade fails on an existing deployment, these queries locate the rows:

    SELECT job_id FROM jobs WHERE attempt_count > max_attempts;
    SELECT job_id FROM jobs
     WHERE started_at IS NOT NULL AND finished_at IS NOT NULL AND finished_at < started_at;
    SELECT job_id FROM job_progress
     WHERE total_processing < 0 OR processing_completed < 0;
    SELECT api_key_id FROM api_keys WHERE key_hash !~ '^[0-9a-f]{64}$';

Revision ID: 008
Revises: 007
Create Date: 2026-08-03
"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

revision: str = "008"
down_revision: Union[str, Sequence[str], None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

# SQLAlchemy's ``index=True`` created these next to the identically-shaped
# ``idx_*`` indexes declared in ``__table_args__``.
_DUPLICATE_INDEXES: tuple[tuple[str, str], ...] = (
    ("documentation_items", "ix_documentation_items_session_id"),
    ("documentation_items", "ix_documentation_items_doc_id"),
    ("documentation_items", "ix_documentation_items_source"),
    ("documentation_items", "ix_documentation_items_created_at"),
    ("jobs", "ix_jobs_session_id"),
    ("jobs", "ix_jobs_status"),
    ("jobs", "ix_jobs_job_type"),
    ("session_data", "ix_session_data_session_id"),
    ("session_data", "ix_session_data_key"),
)

# Indexes no query can use: either their columns are a leading prefix of a unique
# constraint's index, or nothing filters on the column on its own.
_COVERED_INDEXES: tuple[tuple[str, str, list[str], bool], ...] = (
    # Prefixes of idx_jobs_status_type_created.
    ("jobs", "idx_jobs_status", ["status"], False),
    ("jobs", "idx_jobs_type", ["job_type"], False),
    # Prefix of uq_session_data_session_key; session data is always read by
    # (session_id, key) or by session_id.
    ("session_data", "idx_session_data_session_id", ["session_id"], False),
    ("session_data", "idx_session_data_key", ["key"], False),
    # Prefixes of uq_relevant_chunk_unique (session_id, result_key, entity_key,
    # chunk_id, relevant_sequence); relevant chunks are always read session-first.
    ("relevant_chunks", "idx_relevant_chunks_session_id", ["session_id"], False),
    ("relevant_chunks", "idx_relevant_chunks_session_result", ["session_id", "result_key"], False),
    ("relevant_chunks", "idx_relevant_chunks_result_key", ["result_key"], False),
    ("relevant_chunks", "idx_relevant_chunks_entity_key", ["entity_key"], False),
    ("relevant_chunks", "idx_relevant_chunks_doc_id", ["doc_id"], False),
)

# ``ix_*`` leftovers without a duplicate, renamed to the project convention.
_RENAMED_INDEXES: tuple[tuple[str, str], ...] = (
    ("ix_jobs_created_at", "idx_jobs_created_at"),
    ("ix_documentation_items_origin_job_id", "idx_doc_items_origin_job_id"),
)

_REPAIR_RELEVANT_CHUNK_DOC_IDS = sa.text(
    """
    UPDATE relevant_chunks AS rc
    SET doc_id = di.doc_id
    FROM documentation_items AS di
    WHERE di.chunk_id = rc.chunk_id
      AND di.session_id = rc.session_id
      AND di.doc_id IS NOT NULL
      AND rc.doc_id IS DISTINCT FROM di.doc_id
    """
)

_DELETE_UNRESOLVABLE_RELEVANT_CHUNKS = sa.text(
    """
    DELETE FROM relevant_chunks AS rc
    WHERE NOT EXISTS (
        SELECT 1
        FROM documentation_items AS di
        WHERE di.session_id = rc.session_id
          AND di.chunk_id = rc.chunk_id
          AND di.doc_id = rc.doc_id
    )
    """
)


def _upgrade_indexes() -> None:
    for table_name, index_name in _DUPLICATE_INDEXES:
        op.drop_index(index_name, table_name=table_name)

    for table_name, index_name, _columns, _unique in _COVERED_INDEXES:
        op.drop_index(index_name, table_name=table_name)

    for old_name, new_name in _RENAMED_INDEXES:
        op.execute(f"ALTER INDEX {old_name} RENAME TO {new_name}")

    # The claim query is an OR of two disjoint branches ordered by
    # (available_at, created_at, job_id): fresh queued rows and rows whose claim
    # expired. One index per branch can be scanned in that order; the previous
    # combined index could serve neither branch's ordering.
    op.drop_index("idx_jobs_claimable", table_name="jobs")
    op.create_index(
        "idx_jobs_claimable_queued",
        "jobs",
        ["available_at", "created_at", "job_id"],
        unique=False,
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "idx_jobs_claimable_expired",
        "jobs",
        ["claim_expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'running'"),
    )
    # Resolving which chunks a scrape job produced uses JSONB containment, which
    # a btree index cannot answer.
    op.create_index(
        "idx_doc_items_scrape_job_ids_gin",
        "documentation_items",
        ["scrape_job_ids"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"scrape_job_ids": "jsonb_path_ops"},
    )
    op.drop_index("idx_doc_items_metadata_gin", table_name="documentation_items", postgresql_using="gin")


def _upgrade_jobs_columns() -> None:
    """Remove the three redundancies in ``jobs``.

    ``normalized_input`` was a JSONB wrapping the single-key dict
    ``{"sha256": ...}``: the type advertised a document where only a fixed-width
    digest was ever stored, and the reuse index had to carry the JSONB. Rows
    created before the fingerprint existed have no digest, so the new column is
    nullable - and a NULL never matches a lookup, exactly as ``{}`` never did.

    ``waits_for_documentation`` duplicated what the deadline already says. Every
    caller that sets the wait also sets a timeout, so NULL now means "does not
    wait" and no pair of columns can contradict each other. A row that somehow
    waited without a deadline would have been waiting indefinitely; it becomes
    immediately claimable, which is the safe direction.

    ``heartbeat_at`` was written on every heartbeat and read by nothing except
    one field of the job listing. It is ``claim_expires_at`` minus the claim
    timeout, and ``updated_at`` already records the last touch.
    """
    op.add_column("jobs", sa.Column("normalized_input_hash", sa.String(length=64), nullable=True))
    op.execute("UPDATE jobs SET normalized_input_hash = normalized_input ->> 'sha256'")
    op.drop_column("jobs", "normalized_input")

    op.drop_column("jobs", "waits_for_documentation")
    op.drop_column("jobs", "heartbeat_at")

    op.create_index(
        "idx_jobs_reuse_lookup",
        "jobs",
        ["job_type", "normalized_input_hash", "created_at"],
        unique=False,
        postgresql_where=sa.text("status = 'finished'"),
    )


def _upgrade_natural_keys() -> None:
    # job_progress: one row per job, so job_id is the key.
    op.drop_constraint("job_progress_pkey", "job_progress", type_="primary")
    op.drop_column("job_progress", "id")
    op.drop_index("ix_job_progress_job_id", table_name="job_progress")
    op.create_primary_key("job_progress_pkey", "job_progress", ["job_id"])

    # session_data: (session_id, key) is the key the upserts already conflict on.
    op.drop_constraint("session_data_pkey", "session_data", type_="primary")
    op.drop_column("session_data", "id")
    op.drop_constraint("uq_session_data_session_key", "session_data", type_="unique")
    op.create_primary_key("session_data_pkey", "session_data", ["session_id", "key"])


def _upgrade_state_constraints() -> None:
    op.create_check_constraint("check_job_attempt_bounds", "jobs", "attempt_count <= max_attempts")
    op.create_check_constraint(
        "check_job_timeline",
        "jobs",
        "started_at IS NULL OR finished_at IS NULL OR finished_at >= started_at",
    )
    op.create_check_constraint(
        "check_progress_total",
        "job_progress",
        "total_processing IS NULL OR total_processing >= 0",
    )
    op.create_check_constraint(
        "check_progress_completed",
        "job_progress",
        "processing_completed IS NULL OR processing_completed >= 0",
    )
    op.create_check_constraint("check_api_key_hash_hex", "api_keys", "key_hash ~ '^[0-9a-f]{64}$'")


def _repair_relevant_chunks() -> None:
    """Bring relevant_chunks in line with the stored documentation.

    The repair must run before the composite foreign key is added, in both
    execution modes. Only the summary is mode-dependent: offline (``--sql``)
    generation writes the statements to a script instead of executing them, so
    there are no row counts to report and ``execute()`` returns nothing. The
    statements are emitted regardless - skipping them offline would produce a
    script that fails on the foreign key it is supposed to prepare for.
    """
    if context.is_offline_mode():
        op.execute(_REPAIR_RELEVANT_CHUNK_DOC_IDS)
        op.execute(_DELETE_UNRESOLVABLE_RELEVANT_CHUNKS)
        return

    connection = op.get_bind()
    repaired = connection.execute(_REPAIR_RELEVANT_CHUNK_DOC_IDS).rowcount
    deleted = connection.execute(_DELETE_UNRESOLVABLE_RELEVANT_CHUNKS).rowcount
    if repaired or deleted:
        logger.warning(
            "relevant_chunks consistency repair: corrected doc_id on %s row(s), "
            "deleted %s row(s) referencing a chunk outside their session or without a document id",
            repaired,
            deleted,
        )


def _upgrade_reference_integrity() -> None:
    _repair_relevant_chunks()

    # Referenced side of the composite foreign key. chunk_id alone is already the
    # primary key, so this constraint adds no new uniqueness rule; it exists
    # because PostgreSQL requires the referenced columns to be unique.
    op.create_unique_constraint(
        "uq_doc_items_session_chunk_doc",
        "documentation_items",
        ["session_id", "chunk_id", "doc_id"],
    )

    op.drop_constraint("relevant_chunks_chunk_id_fkey", "relevant_chunks", type_="foreignkey")
    op.create_foreign_key(
        "fk_relevant_chunks_documentation_item",
        "relevant_chunks",
        "documentation_items",
        ["session_id", "chunk_id", "doc_id"],
        ["session_id", "chunk_id", "doc_id"],
        ondelete="CASCADE",
    )

    # relevant_sequence holds LLM-authored anchor text of unbounded length, and a
    # btree index tuple is capped at 2704 bytes: a long enough anchor made the
    # INSERT fail with "index row size ... exceeds btree version 4 maximum",
    # aborting the whole extraction write. Indexing its digest keeps the
    # uniqueness rule and makes the stored width irrelevant.
    op.drop_constraint("uq_relevant_chunk_unique", "relevant_chunks", type_="unique")
    op.create_index(
        "uq_relevant_chunk_unique",
        "relevant_chunks",
        ["session_id", "result_key", "entity_key", "chunk_id", sa.text("md5(relevant_sequence::text)")],
        unique=True,
    )

    # Referencing side of the new foreign key, so deleting a documentation item
    # does not scan relevant_chunks.
    op.drop_index("idx_relevant_chunks_chunk_id", table_name="relevant_chunks")
    op.create_index(
        "idx_relevant_chunks_chunk_ref",
        "relevant_chunks",
        ["chunk_id", "session_id", "doc_id"],
        unique=False,
    )

    op.drop_constraint("fk_sessions_api_key_id", "sessions", type_="foreignkey")
    op.create_foreign_key(
        "fk_sessions_api_key_id",
        "sessions",
        "api_keys",
        ["api_key_id"],
        ["api_key_id"],
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    _upgrade_indexes()
    _upgrade_jobs_columns()
    _upgrade_natural_keys()
    _upgrade_state_constraints()
    _upgrade_reference_integrity()


def downgrade() -> None:
    op.drop_constraint("fk_sessions_api_key_id", "sessions", type_="foreignkey")
    op.create_foreign_key(
        "fk_sessions_api_key_id",
        "sessions",
        "api_keys",
        ["api_key_id"],
        ["api_key_id"],
        ondelete="SET NULL",
    )

    op.drop_index("idx_relevant_chunks_chunk_ref", table_name="relevant_chunks")
    op.create_index("idx_relevant_chunks_chunk_id", "relevant_chunks", ["chunk_id"], unique=False)

    op.drop_index("uq_relevant_chunk_unique", table_name="relevant_chunks")
    op.create_unique_constraint(
        "uq_relevant_chunk_unique",
        "relevant_chunks",
        ["session_id", "result_key", "entity_key", "chunk_id", "relevant_sequence"],
    )

    op.drop_constraint("fk_relevant_chunks_documentation_item", "relevant_chunks", type_="foreignkey")
    op.create_foreign_key(
        "relevant_chunks_chunk_id_fkey",
        "relevant_chunks",
        "documentation_items",
        ["chunk_id"],
        ["chunk_id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("uq_doc_items_session_chunk_doc", "documentation_items", type_="unique")

    op.drop_index("idx_jobs_reuse_lookup", table_name="jobs")
    op.add_column("jobs", sa.Column("heartbeat_at", postgresql.TIMESTAMP(timezone=True), nullable=True))
    op.add_column(
        "jobs",
        sa.Column("waits_for_documentation", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.execute("UPDATE jobs SET waits_for_documentation = true WHERE documentation_wait_until IS NOT NULL")
    op.add_column(
        "jobs",
        sa.Column(
            "normalized_input",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.execute(
        "UPDATE jobs SET normalized_input = jsonb_build_object('sha256', normalized_input_hash) "
        "WHERE normalized_input_hash IS NOT NULL"
    )
    op.drop_column("jobs", "normalized_input_hash")
    op.create_index(
        "idx_jobs_reuse_lookup",
        "jobs",
        ["job_type", "normalized_input", "created_at"],
        unique=False,
        postgresql_where=sa.text("status = 'finished'"),
    )

    op.drop_constraint("check_api_key_hash_hex", "api_keys", type_="check")
    op.drop_constraint("check_progress_completed", "job_progress", type_="check")
    op.drop_constraint("check_progress_total", "job_progress", type_="check")
    op.drop_constraint("check_job_timeline", "jobs", type_="check")
    op.drop_constraint("check_job_attempt_bounds", "jobs", type_="check")

    op.drop_constraint("session_data_pkey", "session_data", type_="primary")
    op.add_column(
        "session_data",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
    )
    op.create_primary_key("session_data_pkey", "session_data", ["id"])
    op.create_unique_constraint("uq_session_data_session_key", "session_data", ["session_id", "key"])

    op.drop_constraint("job_progress_pkey", "job_progress", type_="primary")
    op.add_column(
        "job_progress",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
    )
    op.create_primary_key("job_progress_pkey", "job_progress", ["id"])
    op.create_index("ix_job_progress_job_id", "job_progress", ["job_id"], unique=True)

    op.create_index(
        "idx_doc_items_metadata_gin",
        "documentation_items",
        ["metadata"],
        unique=False,
        postgresql_using="gin",
    )
    op.drop_index("idx_doc_items_scrape_job_ids_gin", table_name="documentation_items", postgresql_using="gin")

    op.drop_index("idx_jobs_claimable_expired", table_name="jobs")
    op.drop_index("idx_jobs_claimable_queued", table_name="jobs")
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

    for old_name, new_name in _RENAMED_INDEXES:
        op.execute(f"ALTER INDEX {new_name} RENAME TO {old_name}")

    for table_name, index_name, columns, unique in _COVERED_INDEXES:
        op.create_index(index_name, table_name, columns, unique=unique)

    for table_name, index_name in _DUPLICATE_INDEXES:
        column_name = index_name.removeprefix(f"ix_{table_name}_")
        op.create_index(index_name, table_name, [column_name], unique=False)
