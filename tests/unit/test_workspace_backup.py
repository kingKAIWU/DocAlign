from __future__ import annotations

import sqlite3
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from docalign_core import cli
from docalign_core.domain.workspace_backup import (
    WorkspaceBackupFile,
    WorkspaceBackupFileRole,
    WorkspaceBackupManifest,
)
from docalign_core.workspace_backup import (
    DATABASE_ARCHIVE_PATH,
    EXCLUDED_RUNTIME_DATA,
    WorkspaceBackupError,
    build_workspace_backup,
    file_evidence,
    normalize_workspace_database,
    restore_workspace_backup,
    verify_workspace_backup,
)
from typer.testing import CliRunner


def test_workspace_backup_verifies_and_restores_to_a_new_portable_directory(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    database = source_root / "snapshot.db"
    source = source_root / "uploads" / "doc_1" / "source.docx"
    analysis = source_root / "analyses" / "analysis_1" / "analysis.json"
    output = source_root / "outputs" / "job_1" / "formatted.docx"
    audit = source_root / "jobs" / "job_1" / "audit.json"
    for path, content in (
        (source, b"source"),
        (analysis, b"{}"),
        (output, b"formatted"),
        (audit, b"{}"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    _create_database(database, source_root)

    revision = normalize_workspace_database(database)
    payloads = {
        DATABASE_ARCHIVE_PATH: database,
        "data/uploads/doc_1/source.docx": source,
        "data/analyses/analysis_1/analysis.json": analysis,
        "data/outputs/job_1/formatted.docx": output,
        "data/jobs/job_1/audit.json": audit,
    }
    manifest = _manifest(payloads, revision)
    package = build_workspace_backup(tmp_path / "backup.zip", manifest, payloads)

    verification = verify_workspace_backup(package)
    assert verification.valid is True
    assert verification.file_count == 5
    assert verification.source_document_count == 1
    assert verification.encryption_status == "not_encrypted"
    assert verification.signature_status == "not_signed"

    restored = tmp_path / "restored-workspace"
    receipt = restore_workspace_backup(package, restored)
    assert receipt.backup_id == manifest.backup_id
    assert (restored / "uploads/doc_1/source.docx").read_bytes() == b"source"
    with sqlite3.connect(restored / "docalign.db") as connection:
        stored_path = connection.execute(
            "SELECT stored_path FROM documents WHERE id = 'doc_1'"
        ).fetchone()[0]
        output_path = connection.execute(
            "SELECT output_path FROM jobs WHERE id = 'job_1'"
        ).fetchone()[0]
    assert stored_path == str(restored / "uploads/doc_1/source.docx")
    assert output_path == str(restored / "outputs/job_1/formatted.docx")


def test_workspace_backup_rejects_duplicate_zip_paths(tmp_path: Path) -> None:
    package, _ = _minimal_package(tmp_path)
    with zipfile.ZipFile(package, "a") as archive:
        archive.writestr("README.txt", b"duplicate")

    with pytest.raises(WorkspaceBackupError) as caught:
        verify_workspace_backup(package)
    assert caught.value.code == "WORKSPACE_BACKUP_UNSAFE"


def test_workspace_backup_rejects_path_traversal_and_tampered_payload(tmp_path: Path) -> None:
    unsafe = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(unsafe, "w") as archive:
        archive.writestr("data/../outside", b"not allowed")
    with pytest.raises(WorkspaceBackupError) as traversal:
        verify_workspace_backup(unsafe)
    assert traversal.value.code == "WORKSPACE_BACKUP_UNSAFE"

    package, _ = _minimal_package(tmp_path)
    with zipfile.ZipFile(package) as original:
        entries = [(info, original.read(info)) for info in original.infolist()]
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(tampered, "w") as archive:
        for info, content in entries:
            archive.writestr(
                info,
                content + b"tampered" if info.filename == DATABASE_ARCHIVE_PATH else content,
            )
    with pytest.raises(WorkspaceBackupError) as integrity:
        verify_workspace_backup(tampered)
    assert integrity.value.code == "WORKSPACE_BACKUP_INTEGRITY_FAILED"


def test_workspace_backup_rejects_existing_restore_target(tmp_path: Path) -> None:
    package, _ = _minimal_package(tmp_path)
    target = tmp_path / "existing"
    target.mkdir()

    with pytest.raises(WorkspaceBackupError) as caught:
        restore_workspace_backup(package, target)
    assert caught.value.code == "WORKSPACE_RESTORE_TARGET_EXISTS"


def test_workspace_backup_rejects_active_database_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "active"
    database = root / "state.db"
    _create_database(database, root, job_status="formatting")

    with pytest.raises(WorkspaceBackupError) as caught:
        normalize_workspace_database(database)
    assert caught.value.code == "WORKSPACE_BACKUP_ACTIVE_TASKS"


def test_workspace_backup_cli_verifies_and_restores(tmp_path: Path) -> None:
    package, _ = _minimal_package(tmp_path)
    report = tmp_path / "verification.json"
    runner = CliRunner()

    verified = runner.invoke(
        cli.app,
        ["verify-workspace-backup", str(package), "--report", str(report)],
    )
    assert verified.exit_code == 0, verified.output
    assert "unencrypted · unsigned" in verified.output
    assert report.exists()

    target = tmp_path / "cli-restored"
    restored = runner.invoke(
        cli.app,
        ["restore-workspace-backup", str(package), "--data-dir", str(target)],
    )
    assert restored.exit_code == 0, restored.output
    assert (target / "docalign.db").exists()
    assert "DOCALIGN_DATABASE_URL" in restored.output


def _minimal_package(tmp_path: Path) -> tuple[Path, WorkspaceBackupManifest]:
    root = tmp_path / "minimal"
    database = root / "state.db"
    _create_database(database, root, include_records=False)
    revision = normalize_workspace_database(database)
    payloads = {DATABASE_ARCHIVE_PATH: database}
    manifest = _manifest(payloads, revision)
    return build_workspace_backup(tmp_path / "minimal.zip", manifest, payloads), manifest


def _manifest(payloads: dict[str, Path], revision: str) -> WorkspaceBackupManifest:
    files = []
    for archive_path, path in payloads.items():
        size, digest = file_evidence(path)
        restore_path = archive_path.removeprefix("data/")
        if archive_path == DATABASE_ARCHIVE_PATH:
            role = WorkspaceBackupFileRole.DATABASE
        else:
            role = WorkspaceBackupFileRole(
                archive_path.split("/", 2)[1]
                .replace("uploads", "source_document")
                .replace("analyses", "analysis")
                .replace("outputs", "output")
                .replace("jobs", "job_artifact")
            )
        files.append(
            WorkspaceBackupFile(
                archive_path=archive_path,
                restore_path=restore_path,
                role=role,
                size_bytes=size,
                sha256=digest,
            )
        )
    return WorkspaceBackupManifest(
        backup_id=f"backup_{uuid.uuid4().hex}",
        created_at=datetime.now(UTC),
        application_version="test",
        database_revision=revision,
        database_archive_path=DATABASE_ARCHIVE_PATH,
        files=files,
        excluded_runtime_data=EXCLUDED_RUNTIME_DATA,
    )


def _create_database(
    path: Path,
    root: Path,
    *,
    include_records: bool = True,
    job_status: str = "completed",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE alembic_version (version_num TEXT NOT NULL);
            INSERT INTO alembic_version VALUES ('0006_test');
            CREATE TABLE documents (id TEXT PRIMARY KEY, stored_path TEXT NOT NULL);
            CREATE TABLE analyses (id TEXT PRIMARY KEY, result_path TEXT NOT NULL);
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY, status TEXT NOT NULL, output_path TEXT,
                audit_json_path TEXT, audit_markdown_path TEXT
            );
            CREATE TABLE batch_items (
                id TEXT PRIMARY KEY, status TEXT NOT NULL, current_job_id TEXT
            );
            """
        )
        if include_records:
            connection.execute(
                "INSERT INTO documents VALUES (?, ?)",
                ("doc_1", str(root / "uploads/doc_1/source.docx")),
            )
            connection.execute(
                "INSERT INTO analyses VALUES (?, ?)",
                ("analysis_1", str(root / "analyses/analysis_1/analysis.json")),
            )
            connection.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?)",
                (
                    "job_1",
                    job_status,
                    str(root / "outputs/job_1/formatted.docx"),
                    str(root / "jobs/job_1/audit.json"),
                    None,
                ),
            )
