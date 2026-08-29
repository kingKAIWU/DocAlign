"""Add durable batch processing and retry attempts."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_batch_processing"
down_revision = "0003_rule_pack_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "batches" not in tables:
        op.create_table(
            "batches",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("request_id", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("rule_pack_id", sa.String(length=64), nullable=False),
            sa.Column("rule_pack_revision", sa.Integer(), nullable=False),
            sa.Column("rule_pack_name", sa.String(length=120), nullable=False),
            sa.Column("rule_pack_spec_sha256", sa.String(length=64), nullable=False),
            sa.Column("item_count", sa.Integer(), nullable=False),
            sa.Column("file_manifest_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["rule_pack_id", "rule_pack_revision"],
                ["rule_pack_versions.pack_id", "rule_pack_versions.revision"],
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_batches_request_id", "batches", ["request_id"], unique=True)
    if "batch_items" not in tables:
        op.create_table(
            "batch_items",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("batch_id", sa.String(length=64), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("original_filename", sa.String(length=512), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("source_sha256", sa.String(length=64), nullable=True),
            sa.Column("document_id", sa.String(length=64), nullable=True),
            sa.Column("analysis_id", sa.String(length=64), nullable=True),
            sa.Column("current_job_id", sa.String(length=64), nullable=True),
            sa.Column("attempt_count", sa.Integer(), nullable=False),
            sa.Column("error_code", sa.String(length=128), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["analysis_id"], ["analyses.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["batch_id"], ["batches.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["current_job_id"], ["jobs.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("batch_id", "position"),
        )
        op.create_index("ix_batch_items_batch_id", "batch_items", ["batch_id"])
    if "batch_attempts" not in tables:
        op.create_table(
            "batch_attempts",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("batch_item_id", sa.String(length=64), nullable=False),
            sa.Column("attempt_number", sa.Integer(), nullable=False),
            sa.Column("request_id", sa.String(length=64), nullable=False),
            sa.Column("job_id", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["batch_item_id"], ["batch_items.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("batch_item_id", "attempt_number"),
        )
        op.create_index(
            "ix_batch_attempts_batch_item_id", "batch_attempts", ["batch_item_id"]
        )
        op.create_index(
            "ix_batch_attempts_request_id",
            "batch_attempts",
            ["request_id"],
            unique=True,
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "batch_attempts" in tables:
        op.drop_index("ix_batch_attempts_request_id", table_name="batch_attempts")
        op.drop_index("ix_batch_attempts_batch_item_id", table_name="batch_attempts")
        op.drop_table("batch_attempts")
    if "batch_items" in tables:
        op.drop_index("ix_batch_items_batch_id", table_name="batch_items")
        op.drop_table("batch_items")
    if "batches" in tables:
        op.drop_index("ix_batches_request_id", table_name="batches")
        op.drop_table("batches")
