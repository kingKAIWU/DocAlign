"""Add reusable versioned local rule packs."""

import sqlalchemy as sa
from alembic import op

revision = "0002_rule_pack_library"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0001 historically calls current Base.metadata.create_all(). On a completely
    # fresh database that may already create these tables, so keep this migration
    # idempotent while still upgrading existing 0001 installations.
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "rule_packs" not in tables:
        op.create_table(
            "rule_packs",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("name_key", sa.String(length=240), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("scope_label", sa.String(length=240), nullable=False),
            sa.Column("current_revision", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_rule_packs_name_key", "rule_packs", ["name_key"], unique=True)
    if "rule_pack_versions" not in tables:
        op.create_table(
            "rule_pack_versions",
            sa.Column("pack_id", sa.String(length=64), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("request_id", sa.String(length=64), nullable=False),
            sa.Column("schema_version", sa.String(length=64), nullable=False),
            sa.Column("json_payload", sa.Text(), nullable=False),
            sa.Column("spec_sha256", sa.String(length=64), nullable=False),
            sa.Column("source_type", sa.String(length=64), nullable=False),
            sa.Column("approval_status", sa.String(length=32), nullable=False),
            sa.Column("approval_note", sa.Text(), nullable=True),
            sa.Column("change_note", sa.Text(), nullable=False),
            sa.Column("restored_from_revision", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["pack_id"], ["rule_packs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("pack_id", "revision"),
            sa.UniqueConstraint("pack_id", "revision"),
        )
        op.create_index(
            "ix_rule_pack_versions_request_id",
            "rule_pack_versions",
            ["request_id"],
            unique=True,
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "rule_pack_versions" in tables:
        op.drop_index(
            "ix_rule_pack_versions_request_id", table_name="rule_pack_versions"
        )
        op.drop_table("rule_pack_versions")
    if "rule_packs" in tables:
        op.drop_index("ix_rule_packs_name_key", table_name="rule_packs")
        op.drop_table("rule_packs")
