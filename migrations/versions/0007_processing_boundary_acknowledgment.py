"""Persist complex-content processing-boundary acknowledgments."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_processing_boundary_acknowledgment"
down_revision = "0006_rule_pack_import_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "jobs" in tables:
        job_columns = {column["name"] for column in inspector.get_columns("jobs")}
        if "processing_boundary_acknowledgment" not in job_columns:
            with op.batch_alter_table("jobs") as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "processing_boundary_acknowledgment",
                        sa.String(length=32),
                        nullable=True,
                    )
                )
        if "processing_boundary_acknowledged_at" not in job_columns:
            with op.batch_alter_table("jobs") as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "processing_boundary_acknowledged_at",
                        sa.DateTime(timezone=True),
                        nullable=True,
                    )
                )

    if "batches" in tables:
        batch_columns = {column["name"] for column in inspector.get_columns("batches")}
        if "processing_boundary_acknowledged" not in batch_columns:
            with op.batch_alter_table("batches") as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "processing_boundary_acknowledged",
                        sa.Boolean(),
                        nullable=False,
                        server_default=sa.false(),
                    )
                )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "batches" in tables:
        batch_columns = {column["name"] for column in inspector.get_columns("batches")}
        if "processing_boundary_acknowledged" in batch_columns:
            with op.batch_alter_table("batches") as batch_op:
                batch_op.drop_column("processing_boundary_acknowledged")

    if "jobs" in tables:
        job_columns = {column["name"] for column in inspector.get_columns("jobs")}
        if "processing_boundary_acknowledged_at" in job_columns:
            with op.batch_alter_table("jobs") as batch_op:
                batch_op.drop_column("processing_boundary_acknowledged_at")
        if "processing_boundary_acknowledgment" in job_columns:
            with op.batch_alter_table("jobs") as batch_op:
                batch_op.drop_column("processing_boundary_acknowledgment")
