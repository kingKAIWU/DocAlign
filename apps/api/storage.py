from __future__ import annotations

import shutil
from pathlib import Path


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ("uploads", "analyses", "jobs", "outputs"):
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

    def delete_document_artifacts(
        self, document_id: str, analysis_ids: list[str], job_ids: list[str]
    ) -> None:
        self._remove(self.root / "uploads" / document_id)
        for analysis_id in analysis_ids:
            self._remove(self.root / "analyses" / analysis_id)
        for job_id in job_ids:
            self._remove(self.root / "jobs" / job_id)
            self._remove(self.root / "outputs" / job_id)

    def _remove(self, path: Path) -> None:
        resolved = path.resolve()
        if self.root not in resolved.parents:
            raise ValueError("Refusing to delete a path outside DOCALIGN_DATA_DIR")
        if resolved.exists():
            shutil.rmtree(resolved)
