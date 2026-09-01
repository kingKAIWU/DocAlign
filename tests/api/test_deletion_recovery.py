from __future__ import annotations

import os
from pathlib import Path

import pytest
from docalign_core.config import Settings
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from apps.api.db import Database, DocumentRecord, utcnow
from apps.api.deletions import DeletionManager, DeletionTargetKind
from apps.api.errors import ApiError
from apps.api.main import create_app
from apps.api.storage import LocalStorage

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def test_partial_staging_failure_restores_every_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database = Database(f"sqlite:///{data_dir / 'state.db'}")
    database.create_all()
    storage = LocalStorage(data_dir)
    manager = DeletionManager(database, storage)
    upload = storage.root / "uploads" / "doc_1"
    analysis = storage.root / "analyses" / "analysis_1"
    upload.mkdir(parents=True)
    analysis.mkdir(parents=True)
    (upload / "source.docx").write_bytes(b"source")
    (analysis / "analysis.json").write_bytes(b"analysis")
    original_replace = os.replace

    def fail_analysis_move(source: str | Path, destination: str | Path) -> None:
        if Path(source) == analysis:
            raise OSError("injected move failure")
        original_replace(source, destination)

    monkeypatch.setattr("apps.api.deletions.os.replace", fail_analysis_move)

    with pytest.raises(ApiError) as caught:
        manager.stage(
            DeletionTargetKind.DOCUMENT,
            "doc_1",
            [upload, analysis],
        )

    assert caught.value.code == "LOCAL_DELETE_STAGING_FAILED"
    assert (upload / "source.docx").read_bytes() == b"source"
    assert (analysis / "analysis.json").read_bytes() == b"analysis"
    assert manager.status().pending_operations == 0


def test_committed_delete_is_reported_and_can_be_retried_after_purge_failure(
    academic_docx: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    app = create_app(Settings(data_dir=data_dir, database_url=f"sqlite:///{data_dir / 'state.db'}"))

    with TestClient(app) as client:
        document_id = _upload(client, academic_docx)
        source_dir = data_dir / "uploads" / document_id
        manager = app.state.deletions
        original_purge = manager._purge

        def fail_purge(_manifest: object) -> None:
            raise OSError("injected purge failure")

        monkeypatch.setattr(manager, "_purge", fail_purge)
        deleted = client.delete(f"/api/v1/documents/{document_id}")
        assert deleted.status_code == 204, deleted.text
        assert client.get(f"/api/v1/documents/{document_id}").status_code == 404
        assert not source_dir.exists()

        storage = client.get("/api/v1/workspace/storage").json()
        assert storage["pending_cleanup_operations"] == 1
        assert storage["blocked_cleanup_operations"] == 0
        assert storage["pending_cleanup_bytes"] >= academic_docx.stat().st_size
        assert storage["can_create_backup"] is False
        diagnostic = client.get("/api/v1/diagnostics").json()
        deletion_check = next(
            check for check in diagnostic["checks"] if check["check_id"] == "deletion_recovery"
        )
        assert deletion_check["status"] == "warning"
        assert document_id not in str(deletion_check)
        blocked_backup = client.get("/api/v1/workspace/backup")
        assert blocked_backup.status_code == 409
        assert blocked_backup.json()["error"]["code"] == "WORKSPACE_BACKUP_DELETE_RECOVERY_REQUIRED"

        monkeypatch.setattr(manager, "_purge", original_purge)
        retried = client.post("/api/v1/workspace/cleanup/retry")
        assert retried.status_code == 200, retried.text
        assert retried.json()["purged_operations"] == 1
        assert retried.json()["pending_operations"] == 0
        assert client.get("/api/v1/workspace/storage").json()["pending_cleanup_operations"] == 0


def test_database_commit_failure_restores_staged_document(
    academic_docx: Path, tmp_path: Path
) -> None:
    data_dir = tmp_path / "data"
    app = create_app(Settings(data_dir=data_dir, database_url=f"sqlite:///{data_dir / 'state.db'}"))

    with TestClient(app) as client:
        document_id = _upload(client, academic_docx)
        source = data_dir / "uploads" / document_id / "source.docx"

        def reject_commit(_session: Session) -> None:
            raise RuntimeError("injected database commit failure")

        session_class = app.state.database.session_factory.class_
        event.listen(session_class, "before_commit", reject_commit)
        try:
            with pytest.raises(RuntimeError, match="injected database commit failure"):
                client.delete(f"/api/v1/documents/{document_id}")
        finally:
            event.remove(session_class, "before_commit", reject_commit)

        assert source.read_bytes() == academic_docx.read_bytes()
        assert client.get(f"/api/v1/documents/{document_id}").status_code == 200
        assert client.get("/api/v1/workspace/storage").json()["pending_cleanup_operations"] == 0


def test_startup_restores_uncommitted_staging(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    settings = Settings(
        data_dir=data_dir,
        database_url=f"sqlite:///{data_dir / 'state.db'}",
    )
    database = Database(settings.database_url)
    database.create_all()
    storage = LocalStorage(data_dir)
    document_id = "doc_recovery"
    source = storage.root / "uploads" / document_id / "source.docx"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"recover me")
    with database.session_factory.begin() as session:
        session.add(
            DocumentRecord(
                id=document_id,
                original_filename="recover.docx",
                stored_path=str(source),
                sha256="0" * 64,
                size_bytes=source.stat().st_size,
                created_at=utcnow(),
            )
        )
    manager = DeletionManager(database, storage)
    manager.stage(DeletionTargetKind.DOCUMENT, document_id, [source.parent])
    assert not source.exists()

    with TestClient(create_app(settings)) as client:
        assert source.read_bytes() == b"recover me"
        report = client.get("/api/v1/workspace/storage").json()
        assert report["pending_cleanup_operations"] == 0
        assert client.get(f"/api/v1/documents/{document_id}").status_code == 200


def test_corrupt_deletion_manifest_is_blocked_without_guessing(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    storage = LocalStorage(data_dir)
    database = Database(f"sqlite:///{data_dir / 'state.db'}")
    database.create_all()
    operation_dir = data_dir / ".deletions" / "deletion_corrupt"
    operation_dir.mkdir()
    (operation_dir / "manifest.json").write_text("not-json", encoding="utf-8")
    (operation_dir / "files").mkdir()
    protected = operation_dir / "files" / "unknown.bin"
    protected.write_bytes(b"do not guess")
    manager = DeletionManager(database, storage)

    before = manager.status()
    retried = manager.retry()

    assert before.pending_operations == 1
    assert before.blocked_operations == 1
    assert retried.resolved_operations == 0
    assert retried.pending_operations == 1
    assert retried.blocked_operations == 1
    assert protected.read_bytes() == b"do not guess"


def _upload(client: TestClient, source_path: Path) -> str:
    with source_path.open("rb") as source:
        response = client.post(
            "/api/v1/documents",
            files={"file": ("recovery.docx", source, DOCX_MEDIA_TYPE)},
        )
    assert response.status_code == 201, response.text
    return str(response.json()["document_id"])
