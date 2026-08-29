"""Add retry-safe request identifiers to rule-pack revisions."""

from __future__ import annotations

import hashlib

import sqlalchemy as sa
from alembic import op

revision = "0003_rule_pack_idempotency"
down_revision = "0002_rule_pack_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "rule_pack_versions" not in inspector.get_table_names():
        return

    columns = {item["name"]: item for item in inspector.get_columns("rule_pack_versions")}
    if "request_id" not in columns:
        op.add_column(
            "rule_pack_versions",
            sa.Column("request_id", sa.String(length=64), nullable=True),
        )

    rows = bind.execute(
        sa.text(
            "SELECT pack_id, revision FROM rule_pack_versions "
            "WHERE request_id IS NULL OR request_id = ''"
        )
    ).all()
    for pack_id, revision_number in rows:
        digest = hashlib.sha256(f"{pack_id}:{revision_number}".encode()).hexdigest()[:32]
        bind.execute(
            sa.text(
                "UPDATE rule_pack_versions SET request_id = :request_id "
                "WHERE pack_id = :pack_id AND revision = :revision"
            ),
            {
                "request_id": f"legacy_{digest}",
                "pack_id": pack_id,
                "revision": revision_number,
            },
        )

    columns = {
        item["name"]: item for item in sa.inspect(bind).get_columns("rule_pack_versions")
    }
    if columns["request_id"]["nullable"]:
        with op.batch_alter_table("rule_pack_versions") as batch_op:
            batch_op.alter_column(
                "request_id",
                existing_type=sa.String(length=64),
                nullable=False,
            )

    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("rule_pack_versions")}
    if "ix_rule_pack_versions_request_id" not in indexes:
        op.create_index(
            "ix_rule_pack_versions_request_id",
            "rule_pack_versions",
            ["request_id"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "rule_pack_versions" not in inspector.get_table_names():
        return
    indexes = {item["name"] for item in inspector.get_indexes("rule_pack_versions")}
    if "ix_rule_pack_versions_request_id" in indexes:
        op.drop_index(
            "ix_rule_pack_versions_request_id", table_name="rule_pack_versions"
        )
    columns = {item["name"] for item in sa.inspect(bind).get_columns("rule_pack_versions")}
    if "request_id" in columns:
        with op.batch_alter_table("rule_pack_versions") as batch_op:
            batch_op.drop_column("request_id")
