from __future__ import annotations

import os
import shutil
import stat
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from docalign_core import __version__
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
    verify_workspace_backup,
)
from sqlalchemy import func, select

from apps.api.db import BatchItemRecord, Database, JobRecord
from apps.api.errors import ApiError
from apps.api.storage import LocalStorage

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
_ARTIFACT_ROLES = {
    "uploads": WorkspaceBackupFileRole.SOURCE_DOCUMENT,
    "analyses": WorkspaceBackupFileRole.ANALYSIS,
    "jobs": WorkspaceBackupFileRole.JOB_ARTIFACT,
    "outputs": WorkspaceBackupFileRole.OUTPUT,
    "batches": WorkspaceBackupFileRole.BATCH_ARTIFACT,
}


@dataclass(frozen=True)
class WorkspaceBackupArtifact:
    path: Path
    filename: str
    temporary_directory: Path

    def cleanup(self) -> None:
        shutil.rmtree(self.temporary_directory, ignore_errors=True)


class WorkspaceBackupService:
    def __init__(self, database: Database, storage: LocalStorage) -> None:
        self.database = database
        self.storage = storage

    def create(self) -> WorkspaceBackupArtifact:
        self._assert_quiescent()
        temporary_directory = Path(tempfile.mkdtemp(prefix="docalign-workspace-backup-"))
        try:
            snapshot = temporary_directory / "docalign.db"
            self.database.backup_sqlite(snapshot)
            revision = normalize_workspace_database(snapshot)
            payloads: dict[str, Path] = {DATABASE_ARCHIVE_PATH: snapshot}
            roles: dict[str, WorkspaceBackupFileRole] = {
                DATABASE_ARCHIVE_PATH: WorkspaceBackupFileRole.DATABASE
            }
            for archive_path, source, role in self._artifact_files():
                payloads[archive_path] = source
                roles[archive_path] = role

            files = []
            for archive_path in sorted(payloads):
                size, digest = file_evidence(payloads[archive_path])
                files.append(
                    WorkspaceBackupFile(
                        archive_path=archive_path,
                        restore_path=archive_path.removeprefix("data/"),
                        role=roles[archive_path],
                        size_bytes=size,
                        sha256=digest,
                    )
                )
            created_at = datetime.now(UTC)
            manifest = WorkspaceBackupManifest(
                backup_id=f"backup_{uuid.uuid4().hex}",
                created_at=created_at,
                application_version=__version__,
                database_revision=revision,
                database_archive_path=DATABASE_ARCHIVE_PATH,
                files=files,
                excluded_runtime_data=EXCLUDED_RUNTIME_DATA,
            )
            filename = (
                f"DocAlign-workspace-backup-{created_at:%Y%m%d-%H%M%SZ}-"
                f"{manifest.backup_id[-8:]}.zip"
            )
            package = build_workspace_backup(temporary_directory / filename, manifest, payloads)
            verify_workspace_backup(package)
            return WorkspaceBackupArtifact(package, filename, temporary_directory)
        except WorkspaceBackupError as exc:
            shutil.rmtree(temporary_directory, ignore_errors=True)
            status_code = (
                409
                if exc.code
                in {
                    "WORKSPACE_BACKUP_ACTIVE_TASKS",
                    "WORKSPACE_BACKUP_SOURCE_CHANGED",
                    "WORKSPACE_BACKUP_PAYLOAD_MISMATCH",
                    "WORKSPACE_BACKUP_UNSAFE_SOURCE",
                    "WORKSPACE_BACKUP_SENSITIVE_SOURCE",
                }
                else 500
            )
            raise ApiError(status_code, exc.code, exc.message, exc.details) from exc
        except (OSError, RuntimeError) as exc:
            shutil.rmtree(temporary_directory, ignore_errors=True)
            raise ApiError(
                500,
                "WORKSPACE_BACKUP_FAILED",
                "DocAlign could not create a complete workspace backup.",
            ) from exc

    def active_counts(self) -> tuple[int, int]:
        with self.database.session_factory() as session:
            active_jobs = session.scalar(
                select(func.count()).select_from(JobRecord).where(JobRecord.status.in_(_ACTIVE))
            )
            active_items = session.scalar(
                select(func.count())
                .select_from(BatchItemRecord)
                .where(
                    BatchItemRecord.status == "preparing",
                    BatchItemRecord.current_job_id.is_(None),
                )
            )
        return int(active_jobs or 0), int(active_items or 0)

    def _assert_quiescent(self) -> None:
        active_jobs, active_items = self.active_counts()
        if active_jobs or active_items:
            raise ApiError(
                409,
                "WORKSPACE_BACKUP_ACTIVE_TASKS",
                "Wait for active jobs and batches to finish before creating a backup.",
                {"active_jobs": active_jobs, "active_batch_items": active_items},
            )

    def _artifact_files(
        self,
    ) -> list[tuple[str, Path, WorkspaceBackupFileRole]]:
        result: list[tuple[str, Path, WorkspaceBackupFileRole]] = []
        for directory_name, role in _ARTIFACT_ROLES.items():
            base = self.storage.root / directory_name
            if not base.exists():
                continue
            for current, directory_names, file_names in os.walk(base, followlinks=False):
                current_path = Path(current)
                for directory_name_item in list(directory_names):
                    directory_path = current_path / directory_name_item
                    if directory_path.is_symlink():
                        raise WorkspaceBackupError(
                            "WORKSPACE_BACKUP_UNSAFE_SOURCE",
                            "Symbolic links are not allowed in managed workspace data.",
                        )
                directory_names[:] = [
                    name for name in directory_names if not (current_path / name).is_symlink()
                ]
                for filename in file_names:
                    source = current_path / filename
                    lowered = filename.casefold()
                    if (
                        lowered == ".env"
                        or lowered.startswith(".env.")
                        or lowered
                        in {
                            "credentials",
                            "credentials.json",
                            "secrets.json",
                            "api_key",
                            "api-key",
                        }
                    ):
                        raise WorkspaceBackupError(
                            "WORKSPACE_BACKUP_SENSITIVE_SOURCE",
                            "An environment or credential file was found inside managed data; "
                            "move it outside the workspace artifact directories before backup.",
                        )
                    metadata = source.lstat()
                    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                        raise WorkspaceBackupError(
                            "WORKSPACE_BACKUP_UNSAFE_SOURCE",
                            "Only regular files can be included in a workspace backup.",
                        )
                    relative = source.relative_to(self.storage.root).as_posix()
                    result.append((f"data/{relative}", source, role))
        return result
