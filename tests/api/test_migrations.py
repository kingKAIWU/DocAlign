from __future__ import annotations

from pathlib import Path

from docalign_core.config import Settings
from docalign_core.domain.formatting_spec import default_cleanup_spec
from docalign_core.domain.rule_pack import (
    canonical_formatting_spec_json,
    formatting_spec_sha256,
)
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from apps.api.db import (
    AnalysisRecord,
    Base,
    Database,
    DocumentRecord,
    JobRecord,
    RoleOverrideRecord,
    SpecRecord,
)
from apps.api.main import create_app


def test_rule_pack_migration_upgrades_an_existing_initial_database(tmp_path: Path) -> None:
    database_path = tmp_path / "existing-v1.db"
    database_url = f"sqlite:///{database_path}"
    database = Database(database_url)
    Base.metadata.create_all(
        database.engine,
        tables=[
            DocumentRecord.__table__,
            AnalysisRecord.__table__,
            RoleOverrideRecord.__table__,
            SpecRecord.__table__,
            JobRecord.__table__,
        ],
    )
    with database.engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES ('0001_initial')")
        )

    with TestClient(
        create_app(Settings(data_dir=tmp_path / "data", database_url=database_url))
    ) as client:
        assert client.get("/api/v1/health").status_code == 200

    inspector = inspect(database.engine)
    assert {"rule_packs", "rule_pack_versions"}.issubset(inspector.get_table_names())
    version_columns = {item["name"] for item in inspector.get_columns("rule_pack_versions")}
    assert "request_id" in version_columns
    indexes = inspector.get_indexes("rule_pack_versions")
    assert any(item["name"] == "ix_rule_pack_versions_request_id" for item in indexes)
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0003_rule_pack_idempotency"
        )


def test_startup_backfills_idempotency_for_an_existing_rule_pack_revision(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "existing-rule-pack.db"
    database_url = f"sqlite:///{database_path}"
    database = Database(database_url)
    database.create_all()
    spec = default_cleanup_spec()
    with database.engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_rule_pack_versions_request_id"))
        connection.execute(text("DROP TABLE rule_pack_versions"))
        connection.execute(
            text(
                """
                CREATE TABLE rule_pack_versions (
                    pack_id VARCHAR(64) NOT NULL,
                    revision INTEGER NOT NULL,
                    schema_version VARCHAR(64) NOT NULL,
                    json_payload TEXT NOT NULL,
                    spec_sha256 VARCHAR(64) NOT NULL,
                    source_type VARCHAR(64) NOT NULL,
                    approval_status VARCHAR(32) NOT NULL,
                    approval_note TEXT,
                    change_note TEXT NOT NULL,
                    restored_from_revision INTEGER,
                    created_at DATETIME NOT NULL,
                    PRIMARY KEY (pack_id, revision),
                    FOREIGN KEY(pack_id) REFERENCES rule_packs (id) ON DELETE CASCADE
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO rule_packs (
                    id, name, name_key, description, scope_label,
                    current_revision, created_at, updated_at
                ) VALUES (
                    'pack_legacy', '旧版规则', '旧版规则', '', '旧版内部文档',
                    1, '2026-08-29 00:00:00', '2026-08-29 00:00:00'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO rule_pack_versions (
                    pack_id, revision, schema_version, json_payload, spec_sha256,
                    source_type, approval_status, approval_note, change_note,
                    restored_from_revision, created_at
                ) VALUES (
                    'pack_legacy', 1, 'formatting-spec.v1', :payload, :digest,
                    'preset', 'draft', NULL, '旧版初始修订', NULL,
                    '2026-08-29 00:00:00'
                )
                """
            ),
            {
                "payload": canonical_formatting_spec_json(spec),
                "digest": formatting_spec_sha256(spec),
            },
        )
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        connection.execute(
            text(
                "INSERT INTO alembic_version (version_num) "
                "VALUES ('0002_rule_pack_library')"
            )
        )

    with TestClient(
        create_app(Settings(data_dir=tmp_path / "data", database_url=database_url))
    ) as client:
        response = client.get("/api/v1/rule-packs/pack_legacy/versions/1")
        assert response.status_code == 200, response.text
        assert response.json()["request_id"].startswith("legacy_")
        assert response.json()["spec_sha256"] == formatting_spec_sha256(spec)

    inspector = inspect(database.engine)
    columns = {
        item["name"]: item for item in inspector.get_columns("rule_pack_versions")
    }
    assert columns["request_id"]["nullable"] is False
    assert any(
        item["name"] == "ix_rule_pack_versions_request_id"
        for item in inspector.get_indexes("rule_pack_versions")
    )
