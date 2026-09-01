from __future__ import annotations

import errno
import time
from pathlib import Path

import pytest
from docalign_core.config import Settings
from docalign_core.domain.formatting_spec import default_cleanup_spec
from fastapi.testclient import TestClient

from apps.api import service as service_module
from apps.api.capacity import CapacitySnapshot
from apps.api.main import create_app

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def test_upload_stops_before_writing_when_safe_headroom_is_exhausted(
    academic_docx: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(tmp_path / "upload-capacity")
    with TestClient(app) as client:
        _set_exhausted_snapshot(app, monkeypatch)
        with academic_docx.open("rb") as source:
            response = client.post(
                "/api/v1/documents",
                files={"file": ("容量测试.docx", source, DOCX_MEDIA_TYPE)},
            )

        assert response.status_code == 507
        assert response.json()["error"]["code"] == "WORKSPACE_CAPACITY_INSUFFICIENT"
        assert response.json()["error"]["details"]["operation"] == "document_upload"
        assert not any((app.state.storage.root / "uploads").iterdir())


def test_analysis_stops_before_writing_when_safe_headroom_is_exhausted(
    academic_docx: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(tmp_path / "analysis-capacity")
    with TestClient(app) as client:
        with academic_docx.open("rb") as source:
            uploaded = client.post(
                "/api/v1/documents",
                files={"file": ("分析容量.docx", source, DOCX_MEDIA_TYPE)},
            )
        assert uploaded.status_code == 201, uploaded.text
        _set_exhausted_snapshot(app, monkeypatch)

        response = client.post(
            f"/api/v1/documents/{uploaded.json()['document_id']}/analyze"
        )

        assert response.status_code == 507
        assert response.json()["error"]["details"]["operation"] == "document_analysis"
        assert not any((app.state.storage.root / "analyses").iterdir())


def test_batch_stops_before_creating_durable_records_when_headroom_is_exhausted(
    academic_docx: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(tmp_path / "batch-capacity")
    with TestClient(app) as client:
        pack = client.post(
            "/api/v1/rule-packs",
            json={
                "request_id": "capacity-batch-pack",
                "name": "容量保护规则",
                "scope_label": "批量容量测试",
                "spec": default_cleanup_spec().model_dump(mode="json"),
                "approval_status": "locally_approved",
                "approval_note": "容量路径自动化测试",
            },
        )
        assert pack.status_code == 201, pack.text
        _set_exhausted_snapshot(app, monkeypatch)
        with academic_docx.open("rb") as source:
            response = client.post(
                "/api/v1/batches",
                data={
                    "request_id": "capacity-batch-request",
                    "name": "容量不足批次",
                    "rule_pack_id": pack.json()["pack_id"],
                    "rule_pack_revision": "1",
                    "processing_boundary_acknowledged": "true",
                },
                files=[("files", ("批量容量.docx", source, DOCX_MEDIA_TYPE))],
            )

        assert response.status_code == 507
        payload = response.json()["error"]
        assert payload["code"] == "WORKSPACE_CAPACITY_INSUFFICIENT"
        assert payload["details"]["operation"] == "batch_upload"
        assert client.get("/api/v1/workspace/storage").json()["records"]["batches"] == 0


def test_processing_maps_a_late_disk_full_race_to_a_stable_job_error(
    academic_docx: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(tmp_path / "processing-capacity")

    def disk_full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "simulated disk full after preflight")

    monkeypatch.setattr(service_module, "process_document", disk_full)
    with TestClient(app) as client:
        document_id, analysis_id, spec_id = _prepare_job(client, academic_docx)
        created = client.post(
            "/api/v1/jobs",
            json={
                "document_id": document_id,
                "analysis_id": analysis_id,
                "spec_id": spec_id,
                "processing_boundary_acknowledged": True,
            },
        )
        assert created.status_code == 202, created.text
        job = _wait_for_job(client, created.json()["job_id"])

        assert job["status"] == "failed"
        assert job["error_code"] == "WORKSPACE_CAPACITY_INSUFFICIENT"
        assert "safe working space" in job["error_message"]


def test_processing_preflight_fails_before_creating_output_artifacts(
    academic_docx: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(tmp_path / "processing-preflight")
    with TestClient(app) as client:
        document_id, analysis_id, spec_id = _prepare_job(client, academic_docx)
        original_ensure = app.state.capacity.ensure

        def reject_processing(required_bytes: int, *, operation: str, path=None):
            if operation == "document_processing":
                raise app.state.capacity.api_error(
                    operation=operation,
                    required_bytes=required_bytes,
                )
            return original_ensure(required_bytes, operation=operation, path=path)

        monkeypatch.setattr(app.state.capacity, "ensure", reject_processing)
        created = client.post(
            "/api/v1/jobs",
            json={
                "document_id": document_id,
                "analysis_id": analysis_id,
                "spec_id": spec_id,
                "processing_boundary_acknowledged": True,
            },
        )
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        job = _wait_for_job(client, job_id)

        assert job["status"] == "failed"
        assert job["error_code"] == "WORKSPACE_CAPACITY_INSUFFICIENT"
        assert not (app.state.storage.root / "outputs" / job_id).exists()


def test_backup_reports_estimate_and_refuses_when_temporary_space_is_insufficient(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(tmp_path / "backup-capacity")
    with TestClient(app) as client:
        _set_exhausted_snapshot(app, monkeypatch)
        report = client.get("/api/v1/workspace/storage")
        assert report.status_code == 200
        assert report.json()["write_headroom_bytes"] == 0
        assert report.json()["pressure"] == "critical"
        assert report.json()["can_create_backup"] is False
        assert report.json()["estimated_backup_working_bytes"] > 0

        response = client.get("/api/v1/workspace/backup")
        assert response.status_code == 507
        payload = response.json()["error"]
        assert payload["code"] == "WORKSPACE_CAPACITY_INSUFFICIENT"
        assert payload["details"]["operation"] == "workspace_backup"


def _app(data_dir: Path):
    return create_app(
        Settings(
            data_dir=data_dir,
            database_url=f"sqlite:///{data_dir / 'state.db'}",
            min_free_mb=64,
        )
    )


def _set_exhausted_snapshot(app, monkeypatch: pytest.MonkeyPatch) -> None:
    reserve = app.state.capacity.reserve_bytes
    monkeypatch.setattr(
        app.state.capacity,
        "snapshot",
        lambda path=None: CapacitySnapshot(
            total_bytes=10 * 1024**3,
            free_bytes=reserve,
            reserve_bytes=reserve,
        ),
    )


def _prepare_job(client: TestClient, source_path: Path) -> tuple[str, str, str]:
    with source_path.open("rb") as source:
        uploaded = client.post(
            "/api/v1/documents",
            files={"file": ("处理容量.docx", source, DOCX_MEDIA_TYPE)},
        )
    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()["document_id"]
    analysis = client.post(f"/api/v1/documents/{document_id}/analyze")
    assert analysis.status_code == 201, analysis.text
    spec = client.post(
        "/api/v1/specs",
        json={
            "document_id": document_id,
            "spec": default_cleanup_spec().model_dump(mode="json"),
        },
    )
    assert spec.status_code == 201, spec.text
    return document_id, analysis.json()["analysis_id"], spec.json()["spec_id"]


def _wait_for_job(
    client: TestClient, job_id: str, timeout_seconds: float = 10
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in {"completed", "failed", "canceled"}:
            return payload
        time.sleep(0.02)
    raise AssertionError("job did not reach a terminal state")
