from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from docalign_core.domain.base import StrictModel
from docalign_core.domain.workspace_backup import (
    WorkspaceBackupFile,
    WorkspaceBackupFileRole,
    WorkspaceBackupManifest,
    WorkspaceBackupVerification,
    WorkspaceRestoreReceipt,
)

MANIFEST_PATH = "workspace-backup.json"
PAYLOAD_MANIFEST_PATH = "manifest-sha256.txt"
TAG_MANIFEST_PATH = "tagmanifest-sha256.txt"
README_PATH = "README.txt"
DATABASE_ARCHIVE_PATH = "data/docalign.db"
DATABASE_RESTORE_PATH = "docalign.db"
EXCLUDED_RUNTIME_DATA = [
    "environment files and credentials",
    "runtime locks and process metadata",
    "SQLite WAL and shared-memory files",
    "unmanaged files outside DocAlign artifact directories",
]
README = (
    "DocAlign 可验证完整工作区备份\n\n"
    "此包包含源 DOCX、分析、任务审计、格式化输出、批次产物、规则与数据库记录。\n"
    "可使用 `docalign verify-workspace-backup <package.zip>` 校验，使用\n"
    "`docalign restore-workspace-backup <package.zip> --data-dir <new-directory>` 恢复。\n\n"
    "重要：本包包含敏感原文和文件名，未加密，也没有数字签名。SHA-256 只能验证\n"
    "包内文件是否一致，不能证明创建者身份。请像保护原始文档一样保护本包。\n"
).encode()
_REQUIRED_TAGS = {README_PATH, MANIFEST_PATH, PAYLOAD_MANIFEST_PATH, TAG_MANIFEST_PATH}
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_ACTIVE = {
    "preparing",
    "queued",
    "analyzing",
    "planning",
    "formatting",
    "validating",
    "repairing",
    "canceling",
}
_RESTORE_ROLES = {
    "uploads": WorkspaceBackupFileRole.SOURCE_DOCUMENT,
    "analyses": WorkspaceBackupFileRole.ANALYSIS,
    "jobs": WorkspaceBackupFileRole.JOB_ARTIFACT,
    "outputs": WorkspaceBackupFileRole.OUTPUT,
    "batches": WorkspaceBackupFileRole.BATCH_ARTIFACT,
}


class WorkspaceBackupLimits(StrictModel):
    max_file_bytes: int = 10 * 1024**3
    max_uncompressed_bytes: int = 20 * 1024**3
    max_entries: int = 100_000
    max_compression_ratio: float = 1_000.0


class WorkspaceBackupError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def file_evidence(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def build_workspace_backup(
    target: Path,
    manifest: WorkspaceBackupManifest,
    payloads: Mapping[str, Path],
) -> Path:
    files = _validate_manifest(manifest)
    if set(payloads) != set(files):
        raise WorkspaceBackupError(
            "WORKSPACE_BACKUP_PAYLOAD_MISMATCH",
            "The workspace payload does not match its manifest.",
        )
    for archive_path, item in files.items():
        try:
            size, digest = file_evidence(payloads[archive_path])
        except OSError as exc:
            raise WorkspaceBackupError(
                "WORKSPACE_BACKUP_SOURCE_CHANGED",
                "A workspace file became unavailable during backup.",
                {"archive_path": archive_path},
            ) from exc
        if size != item.size_bytes or digest != item.sha256:
            raise WorkspaceBackupError(
                "WORKSPACE_BACKUP_SOURCE_CHANGED",
                "A workspace file changed during backup; no backup was published.",
                {"archive_path": archive_path},
            )

    manifest_data = (
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    payload_manifest = _checksum_lines({path: item.sha256 for path, item in files.items()})
    tags = {
        README_PATH: README,
        MANIFEST_PATH: manifest_data,
        PAYLOAD_MANIFEST_PATH: payload_manifest,
    }
    tag_manifest = _checksum_lines(
        {name: hashlib.sha256(data).hexdigest() for name, data in tags.items()}
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent, delete=False
    ) as holder:
        temporary = Path(holder.name)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for name in (README_PATH, MANIFEST_PATH, PAYLOAD_MANIFEST_PATH):
                _write_entry(archive, name, tags[name])
            _write_entry(archive, TAG_MANIFEST_PATH, tag_manifest)
            for name in sorted(payloads):
                _write_entry(archive, name, payloads[name])
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def verify_workspace_backup(
    path: Path, limits: WorkspaceBackupLimits | None = None
) -> WorkspaceBackupVerification:
    _, verification = _verify(path, limits or WorkspaceBackupLimits())
    return verification


def restore_workspace_backup(
    package_path: Path,
    data_dir: Path,
    limits: WorkspaceBackupLimits | None = None,
) -> WorkspaceRestoreReceipt:
    manifest, verification = _verify(package_path, limits or WorkspaceBackupLimits())
    destination = data_dir.expanduser().resolve(strict=False)
    if destination.exists() or destination.is_symlink():
        raise WorkspaceBackupError(
            "WORKSPACE_RESTORE_TARGET_EXISTS",
            "Restore requires a new data directory that does not already exist.",
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.restore-", dir=destination.parent))
    os.chmod(staging, 0o700)
    destination_created = False
    try:
        with zipfile.ZipFile(package_path) as archive:
            for item in manifest.files:
                output = staging / item.restore_path
                output.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                with archive.open(item.archive_path) as source, output.open("xb") as target:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        target.write(chunk)
                        size += len(chunk)
                        digest.update(chunk)
                os.chmod(output, 0o600)
                if size != item.size_bytes or digest.hexdigest() != item.sha256:
                    raise WorkspaceBackupError(
                        "WORKSPACE_RESTORE_INTEGRITY_FAILED",
                        "A restored file failed its SHA-256 validation.",
                        {"archive_path": item.archive_path},
                    )
        _rebase_database(staging / DATABASE_RESTORE_PATH, destination)
        for current, _, _ in os.walk(staging):
            os.chmod(current, 0o700)
        try:
            destination.mkdir(mode=0o700)
            destination_created = True
        except FileExistsError as exc:
            raise WorkspaceBackupError(
                "WORKSPACE_RESTORE_TARGET_EXISTS",
                "The restore target was created by another process; nothing was overwritten.",
            ) from exc
        marker = destination / ".docalign-restore-in-progress"
        marker.write_text("Restore is incomplete until this marker is removed.\n", encoding="utf-8")
        os.chmod(marker, 0o600)
        for child in staging.iterdir():
            os.rename(child, destination / child.name)
        marker.unlink()
        staging.rmdir()
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if destination_created:
            shutil.rmtree(destination, ignore_errors=True)
        raise
    return WorkspaceRestoreReceipt(
        backup_id=manifest.backup_id,
        restored_at=datetime.now(UTC),
        database_filename=DATABASE_RESTORE_PATH,
        file_count=verification.file_count,
        payload_bytes=verification.payload_bytes,
        source_document_count=verification.source_document_count,
    )


def normalize_workspace_database(path: Path) -> str:
    """Remove machine-specific paths from an online SQLite snapshot."""
    try:
        with sqlite3.connect(path) as connection:
            _assert_quiescent(connection)
            connection.execute(
                "UPDATE documents SET stored_path = 'uploads/' || id || '/source.docx'"
            )
            connection.execute(
                "UPDATE analyses SET result_path = 'analyses/' || id || '/analysis.json'"
            )
            connection.execute(
                "UPDATE jobs SET output_path = CASE WHEN output_path IS NULL THEN NULL "
                "ELSE 'outputs/' || id || '/formatted.docx' END, "
                "audit_json_path = CASE WHEN audit_json_path IS NULL THEN NULL "
                "ELSE 'jobs/' || id || '/audit.json' END, "
                "audit_markdown_path = CASE WHEN audit_markdown_path IS NULL THEN NULL "
                "ELSE 'jobs/' || id || '/audit.md' END"
            )
            connection.commit()
            revision = _database_revision(connection)
            _assert_database(connection)
        _make_standalone_database(path)
        return revision
    except sqlite3.Error as exc:
        raise WorkspaceBackupError(
            "WORKSPACE_BACKUP_DATABASE_INVALID",
            "The SQLite snapshot could not be normalized or validated.",
        ) from exc


def _verify(
    path: Path, limits: WorkspaceBackupLimits
) -> tuple[WorkspaceBackupManifest, WorkspaceBackupVerification]:
    if not path.is_file():
        raise WorkspaceBackupError("WORKSPACE_BACKUP_NOT_FOUND", "The backup file does not exist.")
    if path.suffix.casefold() != ".zip":
        raise WorkspaceBackupError(
            "WORKSPACE_BACKUP_UNSUPPORTED_FILE", "Only .zip workspace backups are supported."
        )
    if path.stat().st_size > limits.max_file_bytes:
        raise WorkspaceBackupError(
            "WORKSPACE_BACKUP_TOO_LARGE", "The backup exceeds the configured size limit."
        )
    if not zipfile.is_zipfile(path):
        raise WorkspaceBackupError("WORKSPACE_BACKUP_INVALID", "The file is not a valid ZIP.")
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            _validate_zip(infos, limits)
            files = {info.filename: info for info in infos if not info.is_dir()}
            tags = {name for name in files if not name.startswith("data/")}
            if tags != _REQUIRED_TAGS:
                raise WorkspaceBackupError(
                    "WORKSPACE_BACKUP_INCOMPLETE",
                    "The backup is missing required metadata or contains unknown tags.",
                )
            payload_paths = {name for name in files if name.startswith("data/")}
            payload_manifest = _parse_checksum(
                _read_small_entry(archive, PAYLOAD_MANIFEST_PATH), True
            )
            tag_manifest = _parse_checksum(_read_small_entry(archive, TAG_MANIFEST_PATH), False)
            if set(payload_manifest) != payload_paths or set(tag_manifest) != (
                _REQUIRED_TAGS - {TAG_MANIFEST_PATH}
            ):
                raise WorkspaceBackupError(
                    "WORKSPACE_BACKUP_INCOMPLETE", "A checksum manifest is incomplete."
                )
            actual_payload = {name: _hash_entry(archive, files[name]) for name in payload_paths}
            actual_tags = {
                name: hashlib.sha256(_read_small_entry(archive, name)).hexdigest()
                for name in _REQUIRED_TAGS - {TAG_MANIFEST_PATH}
            }
            _assert_hashes(payload_manifest, actual_payload)
            _assert_hashes(tag_manifest, actual_tags)
            manifest = _read_manifest(archive)
            manifest_files = _validate_manifest(manifest)
            if set(manifest_files) != payload_paths:
                raise WorkspaceBackupError(
                    "WORKSPACE_BACKUP_MANIFEST_MISMATCH",
                    "The backup metadata does not list every payload exactly once.",
                )
            for name, item in manifest_files.items():
                if files[name].file_size != item.size_bytes or actual_payload[name] != item.sha256:
                    raise WorkspaceBackupError(
                        "WORKSPACE_BACKUP_MANIFEST_MISMATCH",
                        "The backup metadata does not match a payload file.",
                        {"archive_path": name},
                    )
            _verify_database_entry(
                archive, manifest, set(item.restore_path for item in manifest.files)
            )
            sources = sum(
                item.role == WorkspaceBackupFileRole.SOURCE_DOCUMENT for item in manifest.files
            )
            return manifest, WorkspaceBackupVerification(
                backup_id=manifest.backup_id,
                created_at=manifest.created_at,
                application_version=manifest.application_version,
                database_revision=manifest.database_revision,
                file_count=len(manifest.files),
                payload_bytes=sum(item.size_bytes for item in manifest.files),
                source_document_count=sources,
                warnings=[
                    "This backup contains sensitive source documents and filenames.",
                    "This backup is not encrypted or digitally signed; "
                    "SHA-256 does not prove identity.",
                ],
            )
    except zipfile.BadZipFile as exc:
        raise WorkspaceBackupError(
            "WORKSPACE_BACKUP_INVALID", "The workspace backup ZIP is corrupted."
        ) from exc


def _validate_manifest(manifest: WorkspaceBackupManifest) -> dict[str, WorkspaceBackupFile]:
    if manifest.database_archive_path != DATABASE_ARCHIVE_PATH:
        raise WorkspaceBackupError(
            "WORKSPACE_BACKUP_MANIFEST_INVALID", "The v1 database path is invalid."
        )
    if manifest.excluded_runtime_data != EXCLUDED_RUNTIME_DATA:
        raise WorkspaceBackupError(
            "WORKSPACE_BACKUP_MANIFEST_INVALID", "The v1 exclusion declaration is invalid."
        )
    result: dict[str, WorkspaceBackupFile] = {}
    restore_paths: set[str] = set()
    for item in manifest.files:
        _validate_path(item.archive_path, prefix="data/")
        _validate_path(item.restore_path)
        if item.archive_path != f"data/{item.restore_path}":
            raise WorkspaceBackupError(
                "WORKSPACE_BACKUP_MANIFEST_INVALID", "An archive path is not portable."
            )
        if item.archive_path in result or item.restore_path in restore_paths:
            raise WorkspaceBackupError(
                "WORKSPACE_BACKUP_MANIFEST_INVALID", "The manifest contains duplicate paths."
            )
        expected_role = (
            WorkspaceBackupFileRole.DATABASE
            if item.restore_path == DATABASE_RESTORE_PATH
            else _RESTORE_ROLES.get(PurePosixPath(item.restore_path).parts[0])
        )
        if expected_role is None or item.role != expected_role:
            raise WorkspaceBackupError(
                "WORKSPACE_BACKUP_MANIFEST_INVALID",
                "A backup file role or managed data directory is invalid.",
            )
        result[item.archive_path] = item
        restore_paths.add(item.restore_path)
    database = result.get(DATABASE_ARCHIVE_PATH)
    if database is None or database.role != WorkspaceBackupFileRole.DATABASE:
        raise WorkspaceBackupError(
            "WORKSPACE_BACKUP_MANIFEST_INVALID", "The database payload is missing."
        )
    return result


def _validate_zip(infos: list[zipfile.ZipInfo], limits: WorkspaceBackupLimits) -> None:
    if len(infos) > limits.max_entries:
        raise WorkspaceBackupError("WORKSPACE_BACKUP_UNSAFE", "The backup has too many entries.")
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise WorkspaceBackupError("WORKSPACE_BACKUP_UNSAFE", "The backup has duplicate paths.")
    total = 0
    for info in infos:
        _validate_path(info.filename)
        if info.flag_bits & 0x1 or stat.S_ISLNK(info.external_attr >> 16):
            raise WorkspaceBackupError(
                "WORKSPACE_BACKUP_UNSAFE", "Encrypted files and symbolic links are not supported."
            )
        total += info.file_size
        if total > limits.max_uncompressed_bytes:
            raise WorkspaceBackupError(
                "WORKSPACE_BACKUP_UNSAFE", "The backup expands beyond the configured limit."
            )
        if info.file_size == 0:
            ratio = 1.0
        elif info.compress_size == 0:
            ratio = float("inf")
        else:
            ratio = info.file_size / info.compress_size
        if ratio > limits.max_compression_ratio:
            raise WorkspaceBackupError(
                "WORKSPACE_BACKUP_UNSAFE", "A backup entry exceeds the compression-ratio limit."
            )


def _validate_path(value: str, prefix: str | None = None) -> None:
    candidate = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or ":" in value
        or "\x00" in value
        or any(ord(character) < 32 for character in value)
        or candidate.is_absolute()
        or ".." in candidate.parts
        or candidate.as_posix() != value
        or value.endswith("/")
        or (prefix is not None and not value.startswith(prefix))
    ):
        raise WorkspaceBackupError("WORKSPACE_BACKUP_UNSAFE", "The backup contains an unsafe path.")


def _checksum_lines(entries: Mapping[str, str]) -> bytes:
    return "".join(f"{entries[path]}  {path}\n" for path in sorted(entries)).encode()


def _parse_checksum(data: bytes, payload: bool) -> dict[str, str]:
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise WorkspaceBackupError(
            "WORKSPACE_BACKUP_INVALID", "A checksum manifest is not valid UTF-8."
        ) from exc
    result = {}
    for line in lines:
        if (
            len(line) < 67
            or line[64:66] != "  "
            or any(character not in "0123456789abcdef" for character in line[:64])
        ):
            raise WorkspaceBackupError(
                "WORKSPACE_BACKUP_INVALID", "A checksum manifest line is invalid."
            )
        digest, name = line[:64], line[66:]
        _validate_path(name)
        if name in result or name.startswith("data/") != payload:
            raise WorkspaceBackupError(
                "WORKSPACE_BACKUP_INVALID", "A checksum manifest contains an invalid path."
            )
        result[name] = digest
    if not result:
        raise WorkspaceBackupError("WORKSPACE_BACKUP_INVALID", "A checksum manifest is empty.")
    return result


def _assert_hashes(expected: Mapping[str, str], actual: Mapping[str, str]) -> None:
    mismatched = [name for name in sorted(expected) if expected[name] != actual.get(name)]
    if mismatched:
        raise WorkspaceBackupError(
            "WORKSPACE_BACKUP_INTEGRITY_FAILED",
            "Workspace backup checksum validation failed.",
            {"archive_paths": mismatched[:20]},
        )


def _read_manifest(archive: zipfile.ZipFile) -> WorkspaceBackupManifest:
    try:
        return WorkspaceBackupManifest.model_validate_json(
            _read_small_entry(archive, MANIFEST_PATH)
        )
    except ValidationError as exc:
        raise WorkspaceBackupError(
            "WORKSPACE_BACKUP_MANIFEST_INVALID",
            "The backup metadata does not match workspace-backup.v1.",
        ) from exc


def _read_small_entry(archive: zipfile.ZipFile, name: str) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > 5 * 1024 * 1024:
        raise WorkspaceBackupError(
            "WORKSPACE_BACKUP_MANIFEST_INVALID", "A backup metadata file is unexpectedly large."
        )
    return archive.read(info)


def _hash_entry(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    digest = hashlib.sha256()
    with archive.open(info) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_entry(archive: zipfile.ZipFile, name: str, source: Path | bytes) -> None:
    info = zipfile.ZipInfo(name, _ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100600 << 16
    with archive.open(info, "w", force_zip64=True) as output:
        if isinstance(source, bytes):
            output.write(source)
        else:
            with source.open("rb") as input_stream:
                shutil.copyfileobj(input_stream, output, 1024 * 1024)


def _verify_database_entry(
    archive: zipfile.ZipFile,
    manifest: WorkspaceBackupManifest,
    restore_paths: set[str],
) -> None:
    with tempfile.TemporaryDirectory(prefix="docalign-backup-db-") as directory:
        target = Path(directory) / DATABASE_RESTORE_PATH
        with archive.open(DATABASE_ARCHIVE_PATH) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output, 1024 * 1024)
        try:
            with sqlite3.connect(target) as connection:
                _assert_database(connection)
                if _database_revision(connection) != manifest.database_revision:
                    raise WorkspaceBackupError(
                        "WORKSPACE_BACKUP_DATABASE_INVALID",
                        "The database revision does not match the backup metadata.",
                    )
                _assert_quiescent(connection)
                _assert_portable_paths(connection, restore_paths)
        except sqlite3.Error as exc:
            raise WorkspaceBackupError(
                "WORKSPACE_BACKUP_DATABASE_INVALID", "The backup database is invalid."
            ) from exc


def _assert_database(connection: sqlite3.Connection) -> None:
    row = connection.execute("PRAGMA quick_check").fetchone()
    if row != ("ok",):
        raise WorkspaceBackupError(
            "WORKSPACE_BACKUP_DATABASE_INVALID", "The SQLite integrity check failed."
        )


def _database_revision(connection: sqlite3.Connection) -> str:
    rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    if len(rows) != 1 or not isinstance(rows[0][0], str) or not rows[0][0]:
        raise WorkspaceBackupError(
            "WORKSPACE_BACKUP_DATABASE_INVALID", "The database migration revision is invalid."
        )
    return rows[0][0]


def _assert_quiescent(connection: sqlite3.Connection) -> None:
    placeholders = ",".join("?" for _ in _ACTIVE)
    active_jobs = connection.execute(
        f"SELECT COUNT(*) FROM jobs WHERE status IN ({placeholders})", tuple(_ACTIVE)
    ).fetchone()[0]
    active_items = connection.execute(
        "SELECT COUNT(*) FROM batch_items WHERE status = 'preparing' AND current_job_id IS NULL"
    ).fetchone()[0]
    if active_jobs or active_items:
        raise WorkspaceBackupError(
            "WORKSPACE_BACKUP_ACTIVE_TASKS",
            "Wait for active jobs and batches to finish before creating or restoring a backup.",
            {"active_jobs": active_jobs, "active_batch_items": active_items},
        )


def _assert_portable_paths(connection: sqlite3.Connection, restore_paths: set[str]) -> None:
    expected: list[tuple[str, str]] = []
    expected.extend(
        (path, f"uploads/{identifier}/source.docx")
        for identifier, path in connection.execute("SELECT id, stored_path FROM documents")
    )
    expected.extend(
        (path, f"analyses/{identifier}/analysis.json")
        for identifier, path in connection.execute("SELECT id, result_path FROM analyses")
    )
    for identifier, output, audit_json, audit_markdown in connection.execute(
        "SELECT id, output_path, audit_json_path, audit_markdown_path FROM jobs"
    ):
        if output is not None:
            expected.append((output, f"outputs/{identifier}/formatted.docx"))
        if audit_json is not None:
            expected.append((audit_json, f"jobs/{identifier}/audit.json"))
        if audit_markdown is not None:
            expected.append((audit_markdown, f"jobs/{identifier}/audit.md"))
    mismatched = [
        {"expected_path": wanted, "payload_listed": wanted in restore_paths}
        for actual, wanted in expected
        if actual != wanted or wanted not in restore_paths
    ]
    if mismatched:
        raise WorkspaceBackupError(
            "WORKSPACE_BACKUP_DATABASE_INVALID",
            "Database artifact paths are missing or are not portable.",
            {"paths": mismatched[:20]},
        )


def _rebase_database(database_path: Path, data_dir: Path) -> None:
    with sqlite3.connect(database_path) as connection:
        _assert_portable_paths(
            connection, _database_restore_paths(connection) | {DATABASE_RESTORE_PATH}
        )
        root = str(data_dir)
        connection.execute(
            "UPDATE documents SET stored_path = ? || '/uploads/' || id || '/source.docx'", (root,)
        )
        connection.execute(
            "UPDATE analyses SET result_path = ? || '/analyses/' || id || '/analysis.json'", (root,)
        )
        connection.execute(
            "UPDATE jobs SET output_path = CASE WHEN output_path IS NULL THEN NULL "
            "ELSE ? || '/outputs/' || id || '/formatted.docx' END, "
            "audit_json_path = CASE WHEN audit_json_path IS NULL THEN NULL "
            "ELSE ? || '/jobs/' || id || '/audit.json' END, "
            "audit_markdown_path = CASE WHEN audit_markdown_path IS NULL THEN NULL "
            "ELSE ? || '/jobs/' || id || '/audit.md' END",
            (root, root, root),
        )
        connection.commit()
        _assert_database(connection)
    _make_standalone_database(database_path)


def _database_restore_paths(connection: sqlite3.Connection) -> set[str]:
    paths = {row[0] for row in connection.execute("SELECT stored_path FROM documents")}
    paths.update(row[0] for row in connection.execute("SELECT result_path FROM analyses"))
    for row in connection.execute(
        "SELECT output_path, audit_json_path, audit_markdown_path FROM jobs"
    ):
        paths.update(value for value in row if value is not None)
    return paths


def _make_standalone_database(path: Path) -> None:
    """Vacuum WAL-backed content into one self-contained database file."""
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.", suffix=".standalone", dir=path.parent, delete=False
    ) as holder:
        standalone = Path(holder.name)
    standalone.unlink()
    try:
        with sqlite3.connect(path, timeout=5) as source:
            source.execute("VACUUM INTO ?", (str(standalone),))
        with sqlite3.connect(standalone, timeout=5) as compacted:
            mode = compacted.execute("PRAGMA journal_mode=DELETE").fetchone()
            _assert_database(compacted)
        if mode is None or str(mode[0]).casefold() != "delete":
            raise WorkspaceBackupError(
                "WORKSPACE_BACKUP_DATABASE_INVALID",
                "The SQLite snapshot could not be converted to a standalone file.",
            )
        os.chmod(standalone, 0o600)
        os.replace(standalone, path)
        Path(f"{path}-wal").unlink(missing_ok=True)
        Path(f"{path}-shm").unlink(missing_ok=True)
    finally:
        standalone.unlink(missing_ok=True)
