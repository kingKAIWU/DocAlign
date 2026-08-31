from __future__ import annotations

import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from docalign_core.config import Settings
from docalign_core.domain.formatting_spec import default_cleanup_spec
from docalign_core.workspace_backup import (
    restore_workspace_backup,
    verify_workspace_backup,
)
from fastapi.testclient import TestClient
from sqlalchemy import text

from apps.api.main import create_app

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def test_api_exports_complete_backup_that_reopens_as_a_restored_workspace(
    academic_docx: Path,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "live-workspace"
    settings = Settings(
        data_dir=data_dir,
        database_url=f"sqlite:///{data_dir / 'live.db'}",
    )
    (data_dir / ".env").parent.mkdir(parents=True)
    (data_dir / ".env").write_text("DOCALIGN_LLM_API_KEY=secret", encoding="utf-8")
    (data_dir / "runtime.json").write_text("private runtime metadata", encoding="utf-8")

    with TestClient(create_app(settings)) as client:
        with academic_docx.open("rb") as source:
            uploaded = client.post(
                "/api/v1/documents",
                files={"file": ("待恢复论文.docx", source, DOCX_MEDIA_TYPE)},
            )
        assert uploaded.status_code == 201, uploaded.text
        document_id = uploaded.json()["document_id"]
        analyzed = client.post(f"/api/v1/documents/{document_id}/analyze")
        assert analyzed.status_code == 201, analyzed.text
        spec = client.post(
            "/api/v1/specs",
            json={
                "document_id": document_id,
                "spec": default_cleanup_spec().model_dump(mode="json"),
            },
        )
        job = client.post(
            "/api/v1/jobs",
            json={
                "document_id": document_id,
                "analysis_id": analyzed.json()["analysis_id"],
                "spec_id": spec.json()["spec_id"],
                "processing_boundary_acknowledged": True,
            },
        )
        assert job.status_code == 202, job.text
        job_id = job.json()["job_id"]
        assert _wait_for_status(client, f"/api/v1/jobs/{job_id}")["status"] == "completed"

        rule_pack = client.post(
            "/api/v1/rule-packs",
            json={
                "request_id": "backup-rule-pack",
                "name": "恢复验证规则",
                "scope_label": "工作区恢复测试",
                "spec": default_cleanup_spec().model_dump(mode="json"),
                "approval_status": "locally_approved",
                "approval_note": "测试恢复后规则仍可用",
            },
        )
        assert rule_pack.status_code == 201, rule_pack.text
        pack_id = rule_pack.json()["pack_id"]
        with academic_docx.open("rb") as source:
            batch = client.post(
                "/api/v1/batches",
                data={
                    "request_id": "backup-batch-request",
                    "name": "恢复验证批次",
                    "rule_pack_id": pack_id,
                    "rule_pack_revision": "1",
                    "processing_boundary_acknowledged": "true",
                },
                files=[("files", ("批次恢复.docx", source, DOCX_MEDIA_TYPE))],
            )
        assert batch.status_code == 202, batch.text
        batch_id = batch.json()["batch_id"]
        assert _wait_for_status(client, f"/api/v1/batches/{batch_id}")["status"] == "completed"
        assert client.get(f"/api/v1/batches/{batch_id}/outputs.zip").status_code == 200

        downloaded = client.get("/api/v1/workspace/backup")
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.headers["content-type"] == "application/zip"
        assert "DocAlign-workspace-backup-" in downloaded.headers["content-disposition"]

    package = tmp_path / "downloaded-backup.zip"
    package.write_bytes(downloaded.content)
    verification = verify_workspace_backup(package)
    assert verification.source_document_count == 2
    assert verification.file_count >= 3
    with zipfile.ZipFile(package) as archive:
        names = set(archive.namelist())
        assert "data/docalign.db" in names
        assert not any(name.endswith(".env") for name in names)
        assert not any("runtime.json" in name for name in names)
        assert not any(name.endswith(("-wal", "-shm")) for name in names)

    restored_dir = tmp_path / "restored-workspace"
    receipt = restore_workspace_backup(package, restored_dir)
    assert receipt.source_document_count == 2

    restored_settings = Settings(
        data_dir=restored_dir,
        database_url=f"sqlite:///{restored_dir / 'docalign.db'}",
    )
    with TestClient(create_app(restored_settings)) as restored_client:
        source = restored_client.get(f"/api/v1/documents/{document_id}/source")
        assert source.status_code == 200, source.text
        assert source.content == academic_docx.read_bytes()
        storage = restored_client.get("/api/v1/workspace/storage").json()
        assert storage["records"]["documents"] == 2
        assert storage["records"]["analyses"] == 2
        assert storage["records"]["jobs"] == 2
        assert storage["records"]["batches"] == 1
        assert storage["records"]["rule_packs"] == 1
        assert restored_client.get(f"/api/v1/jobs/{job_id}/output").status_code == 200
        assert restored_client.get(f"/api/v1/batches/{batch_id}").status_code == 200
        catalog = restored_client.get("/api/v1/rule-packs").json()
        assert any(item["pack_id"] == pack_id for item in catalog["rule_packs"])


def test_api_refuses_backup_while_a_job_is_active(tmp_path: Path) -> None:
    data_dir = tmp_path / "active-workspace"
    app = create_app(Settings(data_dir=data_dir, database_url=f"sqlite:///{data_dir / 'state.db'}"))
    with TestClient(app) as client:
        now = datetime.now(UTC).isoformat()
        with app.state.database.engine.begin() as connection:
            connection.execute(text("PRAGMA foreign_keys=OFF"))
            connection.execute(
                text(
                    "INSERT INTO jobs "
                    "(id, document_id, analysis_id, spec_id, status, progress, "
                    "cancel_requested, created_at, updated_at) "
                    "VALUES ('job_active', 'missing_document', 'missing_analysis', "
                    "'missing_spec', 'formatting', 50, 0, :now, :now)"
                ),
                {"now": now},
            )

        response = client.get("/api/v1/workspace/backup")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "WORKSPACE_BACKUP_ACTIVE_TASKS"
        assert response.json()["error"]["details"]["active_jobs"] == 1


def test_api_refuses_environment_files_inside_managed_artifacts(tmp_path: Path) -> None:
    data_dir = tmp_path / "unsafe-workspace"
    app = create_app(Settings(data_dir=data_dir, database_url=f"sqlite:///{data_dir / 'state.db'}"))
    with TestClient(app) as client:
        misplaced = data_dir / "uploads" / "manual" / ".env"
        misplaced.parent.mkdir(parents=True)
        misplaced.write_text("API_KEY=must-not-export", encoding="utf-8")

        response = client.get("/api/v1/workspace/backup")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "WORKSPACE_BACKUP_SENSITIVE_SOURCE"


def _wait_for_status(client: TestClient, url: str) -> dict[str, object]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        response = client.get(url)
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in {"completed", "completed_with_errors", "failed", "canceled"}:
            return payload
        time.sleep(0.02)
    raise AssertionError(f"Timed out waiting for {url}")
