"""Add portable rule-pack import provenance and deduplication."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_rule_pack_import_provenance"
down_revision = "0005_batch_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "rule_pack_versions" not in inspector.get_table_names():
        return

    columns = {item["name"] for item in inspector.get_columns("rule_pack_versions")}
    with op.batch_alter_table("rule_pack_versions") as batch_op:
        if "import_source_json" not in columns:
            batch_op.add_column(sa.Column("import_source_json", sa.Text(), nullable=True))
        if "import_source_artifact_sha256" not in columns:
            batch_op.add_column(
                sa.Column(
                    "import_source_artifact_sha256",
                    sa.String(length=64),
                    nullable=True,
                )
            )

    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("rule_pack_versions")}
    if "ix_rule_pack_versions_import_source_artifact_sha256" not in indexes:
        op.create_index(
            "ix_rule_pack_versions_import_source_artifact_sha256",
            "rule_pack_versions",
            ["import_source_artifact_sha256"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "rule_pack_versions" not in inspector.get_table_names():
        return
    indexes = {item["name"] for item in inspector.get_indexes("rule_pack_versions")}
    if "ix_rule_pack_versions_import_source_artifact_sha256" in indexes:
        op.drop_index(
            "ix_rule_pack_versions_import_source_artifact_sha256",
            table_name="rule_pack_versions",
        )
    columns = {item["name"] for item in sa.inspect(bind).get_columns("rule_pack_versions")}
    with op.batch_alter_table("rule_pack_versions") as batch_op:
        if "import_source_artifact_sha256" in columns:
            batch_op.drop_column("import_source_artifact_sha256")
        if "import_source_json" in columns:
            batch_op.drop_column("import_source_json")
