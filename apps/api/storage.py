from __future__ import annotations

import os
import shutil
from pathlib import Path

from docalign_core.domain.workspace import StorageCategory, StorageCategoryId


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ("uploads", "analyses", "jobs", "outputs", "batches"):
            (self.root / name).mkdir(exist_ok=True)

    def upload_path(self, document_id: str) -> Path:
        path = self.root / "uploads" / document_id / "source.docx"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def analysis_path(self, analysis_id: str) -> Path:
        path = self.root / "analyses" / analysis_id / "analysis.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def job_dir(self, job_id: str) -> Path:
        path = self.root / "jobs" / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def output_path(self, job_id: str) -> Path:
        path = self.root / "outputs" / job_id / "formatted.docx"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def batch_dir(self, batch_id: str) -> Path:
        path = self.root / "batches" / batch_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def batch_output_zip_path(self, batch_id: str) -> Path:
        return self.batch_dir(batch_id) / "outputs.zip"

    def delete_document_artifacts(
        self, document_id: str, analysis_ids: list[str], job_ids: list[str]
    ) -> None:
        self._remove(self.root / "uploads" / document_id)
        for analysis_id in analysis_ids:
            self._remove(self.root / "analyses" / analysis_id)
        for job_id in job_ids:
            self._remove(self.root / "jobs" / job_id)
            self._remove(self.root / "outputs" / job_id)

    def delete_batch_artifacts(self, batch_id: str) -> None:
        self._remove(self.root / "batches" / batch_id)

    def delete_job_artifacts(self, job_id: str) -> None:
        self._remove(self.root / "jobs" / job_id)
        self._remove(self.root / "outputs" / job_id)

    def usage_categories(self) -> list[StorageCategory]:
        category_paths = {
            StorageCategoryId.SOURCE_DOCUMENTS: [self.root / "uploads"],
            StorageCategoryId.ANALYSES: [self.root / "analyses"],
            StorageCategoryId.JOB_AUDITS: [self.root / "jobs"],
            StorageCategoryId.OUTPUTS: [self.root / "outputs"],
            StorageCategoryId.BATCH_PACKAGES: [self.root / "batches"],
        }
        database_paths: list[Path] = []
        other_paths: list[Path] = []
        known_names = {path.name for paths in category_paths.values() for path in paths}
        try:
            root_entries = list(self.root.iterdir())
        except OSError:
            root_entries = []
        for path in root_entries:
            if path.name in known_names:
                continue
            if path.is_file() and _looks_like_sqlite_file(path.name):
                database_paths.append(path)
            else:
                other_paths.append(path)
        category_paths[StorageCategoryId.DATABASE] = database_paths
        category_paths[StorageCategoryId.OTHER] = other_paths

        result: list[StorageCategory] = []
        for category in StorageCategoryId:
            size, file_count = self.usage_for_paths(category_paths.get(category, []))
            result.append(
                StorageCategory(
                    category=category,
                    bytes=size,
                    file_count=file_count,
                )
            )
        return result

    def disk_capacity(self) -> tuple[int, int]:
        usage = shutil.disk_usage(self.root)
        return usage.total, usage.free

    def usage_for_paths(self, paths: list[Path]) -> tuple[int, int]:
        total_bytes = 0
        total_files = 0
        seen: set[Path] = set()
        for path in paths:
            if path.is_symlink():
                continue
            resolved = path.resolve(strict=False)
            if resolved in seen:
                continue
            if resolved != self.root and self.root not in resolved.parents:
                raise ValueError("Refusing to inspect a path outside DOCALIGN_DATA_DIR")
            seen.add(resolved)
            size, file_count = _path_usage(path)
            total_bytes += size
            total_files += file_count
        return total_bytes, total_files

    def document_artifact_bytes(
        self, document_id: str, analysis_ids: list[str], job_ids: list[str]
    ) -> int:
        paths = [self.root / "uploads" / document_id]
        paths.extend(self.root / "analyses" / analysis_id for analysis_id in analysis_ids)
        for job_id in job_ids:
            paths.append(self.root / "jobs" / job_id)
            paths.append(self.root / "outputs" / job_id)
        return self.usage_for_paths(paths)[0]

    def batch_artifact_bytes(
        self,
        batch_id: str,
        document_artifacts: list[tuple[str, list[str], list[str]]],
    ) -> int:
        paths = [self.root / "batches" / batch_id]
        for document_id, analysis_ids, job_ids in document_artifacts:
            paths.append(self.root / "uploads" / document_id)
            paths.extend(
                self.root / "analyses" / analysis_id for analysis_id in analysis_ids
            )
            for job_id in job_ids:
                paths.append(self.root / "jobs" / job_id)
                paths.append(self.root / "outputs" / job_id)
        return self.usage_for_paths(paths)[0]

    def _remove(self, path: Path) -> None:
        resolved = path.resolve()
        if self.root not in resolved.parents:
            raise ValueError("Refusing to delete a path outside DOCALIGN_DATA_DIR")
        if resolved.exists():
            shutil.rmtree(resolved)


def _looks_like_sqlite_file(filename: str) -> bool:
    lowered = filename.lower()
    return lowered.endswith((".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm"))


def _path_usage(path: Path) -> tuple[int, int]:
    try:
        if path.is_symlink():
            return 0, 0
        if path.is_file():
            return path.stat().st_size, 1
        if not path.is_dir():
            return 0, 0
    except OSError:
        return 0, 0

    total_bytes = 0
    total_files = 0
    pending = [path]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total_bytes += entry.stat(follow_symlinks=False).st_size
                            total_files += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return total_bytes, total_files
