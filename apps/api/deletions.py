from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from docalign_core.domain.base import StrictModel
from docalign_core.domain.workspace import CleanupRecoveryReport
from pydantic import ValidationError

from apps.api.db import BatchRecord, Database, DocumentRecord
from apps.api.errors import ApiError
from apps.api.storage import LocalStorage

logger = logging.getLogger(__name__)

_MANIFEST_NAME = "manifest.json"
_MANAGED_DIRECTORIES = {"uploads", "analyses", "jobs", "outputs", "batches"}


class DeletionTargetKind(StrEnum):
    DOCUMENT = "document"
    BATCH = "batch"


class DeletionPhase(StrEnum):
    PLANNED = "planned"
    STAGED = "staged"
    COMMITTED = "committed"


class ArtifactDeletionManifest(StrictModel):
    schema_version: Literal["artifact-deletion.v1"] = "artifact-deletion.v1"
    operation_id: str
    target_kind: DeletionTargetKind
    target_id: str
    phase: DeletionPhase
    created_at: datetime
    artifact_paths: list[str]


@dataclass(frozen=True)
class StagedDeletion:
    manifest: ArtifactDeletionManifest


class DeletionManager:
    """Crash-recoverable artifact deletion journal on the workspace volume."""

    def __init__(self, database: Database, storage: LocalStorage) -> None:
        self.database = database
        self.storage = storage
        self.root = storage.root / ".deletions"
        self.root.mkdir(exist_ok=True)
        self._lock = threading.RLock()
        self._scan_failed = False
        self._invalid_entries = 0

    def stage(
        self,
        target_kind: DeletionTargetKind,
        target_id: str,
        paths: list[Path],
    ) -> StagedDeletion:
        with self._lock:
            relative_paths = self._relative_paths(paths)
            operation_id = f"deletion_{uuid.uuid4().hex}"
            manifest = ArtifactDeletionManifest(
                operation_id=operation_id,
                target_kind=target_kind,
                target_id=target_id,
                phase=DeletionPhase.PLANNED,
                created_at=datetime.now(UTC),
                artifact_paths=relative_paths,
            )
            operation_dir = self.root / operation_id
            try:
                (operation_dir / "files").mkdir(parents=True)
                self._write_manifest(manifest)
                for relative in relative_paths:
                    source = self.storage.root / relative
                    if source.is_symlink():
                        raise OSError("Managed artifact directory is a symbolic link")
                    if not os.path.lexists(source):
                        continue
                    if not source.is_dir():
                        raise OSError("Managed artifact path is not a directory")
                    destination = operation_dir / "files" / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(source, destination)
                manifest = manifest.model_copy(update={"phase": DeletionPhase.STAGED})
                self._write_manifest(manifest)
                return StagedDeletion(manifest=manifest)
            except (OSError, ValueError) as exc:
                restored = self._restore(manifest)
                if restored:
                    raise ApiError(
                        500,
                        "LOCAL_DELETE_STAGING_FAILED",
                        "DocAlign could not prepare this deletion; database records and "
                        "files were preserved.",
                    ) from exc
                raise ApiError(
                    500,
                    "LOCAL_DELETE_RECOVERY_REQUIRED",
                    "Deletion stopped after a local file error. Use the storage center to "
                    "retry safe cleanup.",
                    {"operation_id": operation_id},
                ) from exc

    def rollback(self, staged: StagedDeletion) -> None:
        with self._lock:
            if not self._restore(staged.manifest):
                raise ApiError(
                    500,
                    "LOCAL_DELETE_RECOVERY_REQUIRED",
                    "The database change was rolled back, but some files still need safe recovery.",
                    {"operation_id": staged.manifest.operation_id},
                )

    def finalize(self, staged: StagedDeletion) -> bool:
        """Return True when cleanup remains pending after the logical deletion."""
        with self._lock:
            committed = staged.manifest.model_copy(update={"phase": DeletionPhase.COMMITTED})
            try:
                self._write_manifest(committed)
                self._purge(committed)
            except OSError:
                logger.exception(
                    "Artifact cleanup remains pending for %s", staged.manifest.operation_id
                )
                return True
            return False

    def retry(self) -> CleanupRecoveryReport:
        with self._lock:
            restored = 0
            purged = 0
            for operation_dir in self._operation_directories():
                manifest = self._read_manifest(operation_dir)
                if manifest is None:
                    continue
                try:
                    if manifest.phase == DeletionPhase.COMMITTED or not self._target_exists(
                        manifest
                    ):
                        self._purge(manifest)
                        purged += 1
                    elif self._restore(manifest):
                        restored += 1
                except OSError:
                    logger.exception("Could not reconcile deletion %s", operation_dir.name)
            status = self.status()
            return CleanupRecoveryReport(
                resolved_operations=restored + purged,
                restored_operations=restored,
                purged_operations=purged,
                pending_operations=status.pending_operations,
                blocked_operations=status.blocked_operations,
                pending_bytes=status.pending_bytes,
            )

    def status(self) -> CleanupRecoveryReport:
        with self._lock:
            directories = self._operation_directories()
            blocked = (
                sum(self._read_manifest(directory) is None for directory in directories)
                + self._invalid_entries
                + int(self._scan_failed)
            )
            pending_bytes = self.storage.usage_for_paths([self.root])[0]
            return CleanupRecoveryReport(
                resolved_operations=0,
                restored_operations=0,
                purged_operations=0,
                pending_operations=(
                    len(directories) + self._invalid_entries + int(self._scan_failed)
                ),
                blocked_operations=blocked,
                pending_bytes=pending_bytes,
            )

    def _target_exists(self, manifest: ArtifactDeletionManifest) -> bool:
        model = (
            DocumentRecord if manifest.target_kind == DeletionTargetKind.DOCUMENT else BatchRecord
        )
        with self.database.session_factory() as session:
            return session.get(model, manifest.target_id) is not None

    def _relative_paths(self, paths: list[Path]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for path in paths:
            try:
                relative = path.relative_to(self.storage.root)
            except ValueError as exc:
                raise ValueError("Deletion path is outside DOCALIGN_DATA_DIR") from exc
            if (
                len(relative.parts) < 2
                or relative.parts[0] not in _MANAGED_DIRECTORIES
                or any(part in {"", ".", ".."} for part in relative.parts)
            ):
                raise ValueError("Deletion path is not a managed artifact directory")
            value = relative.as_posix()
            if value not in seen:
                seen.add(value)
                result.append(value)
        return result

    def _operation_directories(self) -> list[Path]:
        try:
            entries = list(self.root.iterdir())
        except OSError:
            self._scan_failed = True
            self._invalid_entries = 0
            logger.exception("Could not inspect the deletion recovery directory")
            return []
        self._scan_failed = False
        directories: list[Path] = []
        invalid_entries = 0
        for path in entries:
            try:
                if path.is_dir() and not path.is_symlink():
                    directories.append(path)
                else:
                    invalid_entries += 1
            except OSError:
                invalid_entries += 1
        self._invalid_entries = invalid_entries
        return sorted(directories)

    def _manifest_path(self, operation_id: str) -> Path:
        return self.root / operation_id / _MANIFEST_NAME

    def _write_manifest(self, manifest: ArtifactDeletionManifest) -> None:
        target = self._manifest_path(manifest.operation_id)
        temporary = target.with_name(f".{_MANIFEST_NAME}.{uuid.uuid4().hex}.tmp")
        payload = json.dumps(
            manifest.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
        ).encode("utf-8")
        try:
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def _read_manifest(self, operation_dir: Path) -> ArtifactDeletionManifest | None:
        try:
            payload = json.loads((operation_dir / _MANIFEST_NAME).read_text("utf-8"))
            manifest = ArtifactDeletionManifest.model_validate(payload)
            if manifest.operation_id != operation_dir.name:
                return None
            self._relative_paths(
                [self.storage.root / relative for relative in manifest.artifact_paths]
            )
            return manifest
        except (OSError, ValueError, json.JSONDecodeError, ValidationError):
            return None

    def _restore(self, manifest: ArtifactDeletionManifest) -> bool:
        operation_dir = self.root / manifest.operation_id
        try:
            if not os.path.lexists(operation_dir):
                return True
            for relative in reversed(manifest.artifact_paths):
                destination = operation_dir / "files" / relative
                if not os.path.lexists(destination):
                    continue
                source = self.storage.root / relative
                if os.path.lexists(source):
                    raise OSError("Recovery target already exists")
                source.parent.mkdir(parents=True, exist_ok=True)
                os.replace(destination, source)
            shutil.rmtree(operation_dir)
            return True
        except OSError:
            logger.exception("Could not restore deletion %s", manifest.operation_id)
            return False

    def _purge(self, manifest: ArtifactDeletionManifest) -> None:
        operation_dir = self.root / manifest.operation_id
        if not os.path.lexists(operation_dir):
            return
        files = operation_dir / "files"
        if files.exists():
            shutil.rmtree(files)
        (operation_dir / _MANIFEST_NAME).unlink(missing_ok=True)
        operation_dir.rmdir()
