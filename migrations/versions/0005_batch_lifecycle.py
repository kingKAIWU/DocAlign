"""Add cooperative cancellation state for jobs and batches."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_batch_lifecycle"
down_revision = "0004_batch_processing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    job_columns = {column["name"] for column in inspector.get_columns("jobs")}
    if "cancel_requested" not in job_columns:
        with op.batch_alter_table("jobs") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "cancel_requested",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )

    batch_columns = {column["name"] for column in inspector.get_columns("batches")}
    if "cancel_requested_at" not in batch_columns:
        with op.batch_alter_table("batches") as batch_op:
            batch_op.add_column(
                sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True)
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    batch_columns = {column["name"] for column in inspector.get_columns("batches")}
    if "cancel_requested_at" in batch_columns:
        with op.batch_alter_table("batches") as batch_op:
            batch_op.drop_column("cancel_requested_at")

    job_columns = {column["name"] for column in inspector.get_columns("jobs")}
    if "cancel_requested" in job_columns:
        with op.batch_alter_table("jobs") as batch_op:
            batch_op.drop_column("cancel_requested")
