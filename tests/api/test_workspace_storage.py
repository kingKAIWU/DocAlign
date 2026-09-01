from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from docalign_core.config import Settings
from docalign_core.domain.formatting_spec import default_cleanup_spec
from fastapi.testclient import TestClient

from apps.api import service as service_module
from apps.api.main import create_app
from apps.api.storage import LocalStorage

DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def test_storage_report_protects_active_documents_and_lists_cleanup_candidates(
    academic_docx: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "storage-center"
    app = create_app(
        Settings(data_dir=data_dir, database_url=f"sqlite:///{data_dir / 'state.db'}")
    )
    started = threading.Event()
    release = threading.Event()
    original_process = service_module.process_document
    process_calls = 0

    def block_first_process(*args, **kwargs):
        nonlocal process_calls
        process_calls += 1
        if process_calls == 1:
            started.set()
            if not release.wait(5):
                raise AssertionError("test did not release the active document job")
        return original_process(*args, **kwargs)

    monkeypatch.setattr(service_module, "process_document", block_first_process)

    with TestClient(app) as client:
        document_id, job_id = _create_document_job(client, academic_docx)
        assert started.wait(5)
        active = client.get("/api/v1/workspace/storage")
        assert active.status_code == 200, active.text
        active_payload = active.json()
        assert active_payload["schema_version"] == "workspace-storage.v1"
        assert active_payload["records"]["documents"] == 1
        assert active_payload["records"]["jobs"] == 1
        assert active_payload["unbatched_documents"][0]["document_id"] == document_id
        assert active_payload["unbatched_documents"][0]["deletable"] is False
        assert active_payload["unbatched_documents"][0]["active_job_count"] == 1

        blocked_delete = client.delete(f"/api/v1/documents/{document_id}")
        assert blocked_delete.status_code == 409
        assert blocked_delete.json()["error"]["code"] == "DOCUMENT_JOB_ACTIVE"

        release.set()
        assert _wait_for_job(client, job_id)["status"] == "completed"
        terminal_document = client.get("/api/v1/workspace/storage").json()
        assert terminal_document["unbatched_documents"][0]["deletable"] is True
        assert terminal_document["unbatched_documents"][0]["bytes"] >= academic_docx.stat().st_size

        pack = client.post(
            "/api/v1/rule-packs",
            json={
                "request_id": "storage-center-pack",
                "name": "存储中心测试规则",
                "scope_label": "存储中心测试文档",
                "spec": default_cleanup_spec().model_dump(mode="json"),
                "approval_status": "locally_approved",
                "approval_note": "自动化测试逐项核对",
            },
        )
        assert pack.status_code == 201, pack.text
        with academic_docx.open("rb") as source:
            batch = client.post(
                "/api/v1/batches",
                data={
                    "request_id": "storage-center-batch",
                    "name": "可清理终态批次",
                    "rule_pack_id": pack.json()["pack_id"],
                    "rule_pack_revision": "1",
                    "processing_boundary_acknowledged": "true",
                },
                files=[("files", ("批次文档.docx", source, DOCX_MEDIA_TYPE))],
            )
        assert batch.status_code == 202, batch.text
        batch_id = batch.json()["batch_id"]
        assert _wait_for_batch(client, batch_id)["status"] == "completed"

        report = client.get("/api/v1/workspace/storage").json()
        assert report["records"] == {
            "documents": 2,
            "analyses": 2,
            "jobs": 2,
            "active_jobs": 0,
            "batches": 1,
            "active_batches": 0,
            "rule_packs": 1,
        }
        assert report["terminal_batches"][0]["batch_id"] == batch_id
        assert report["terminal_batches"][0]["bytes"] >= academic_docx.stat().st_size
        assert report["reclaimable_bytes"] > 0
        assert report["docalign_bytes"] == sum(
            category["bytes"] for category in report["categories"]
        )
        assert {category["category"] for category in report["categories"]} == {
            "source_documents",
            "analyses",
            "job_audits",
            "outputs",
            "batch_packages",
            "pending_cleanup",
            "database",
            "other",
        }
        assert report["disk_total_bytes"] > report["disk_free_bytes"] > 0
        assert client.get("/api/v1/workspace/storage?item_limit=0").status_code == 422

        assert client.delete(f"/api/v1/documents/{document_id}").status_code == 204
        assert client.delete(f"/api/v1/batches/{batch_id}").status_code == 204
        cleaned = client.get("/api/v1/workspace/storage").json()
        assert cleaned["records"]["documents"] == 0
        assert cleaned["records"]["batches"] == 0
        assert cleaned["records"]["rule_packs"] == 1
        assert cleaned["terminal_batches"] == []
        assert cleaned["unbatched_documents"] == []


def test_storage_usage_does_not_follow_symlinks_outside_data_dir(
    tmp_path: Path,
) -> None:
    storage = LocalStorage(tmp_path / "data")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.docx").write_bytes(b"private" * 100)
    link = storage.root / "uploads" / "outside-link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")

    sources = next(
        item
        for item in storage.usage_categories()
        if item.category.value == "source_documents"
    )
    assert sources.bytes == 0
    assert sources.file_count == 0
    with pytest.raises(ValueError, match="outside DOCALIGN_DATA_DIR"):
        storage.usage_for_paths([outside])


def _create_document_job(client: TestClient, source_path: Path) -> tuple[str, str]:
    with source_path.open("rb") as source:
        uploaded = client.post(
            "/api/v1/documents",
            files={"file": ("独立文档.docx", source, DOCX_MEDIA_TYPE)},
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
    job = client.post(
        "/api/v1/jobs",
        json={
            "document_id": document_id,
            "analysis_id": analysis.json()["analysis_id"],
            "spec_id": spec.json()["spec_id"],
            "processing_boundary_acknowledged": True,
        },
    )
    assert job.status_code == 202, job.text
    return document_id, job.json()["job_id"]


def _wait_for_job(
    client: TestClient, job_id: str, timeout_seconds: float = 10
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        payload = client.get(f"/api/v1/jobs/{job_id}").json()
        if payload["status"] in {"completed", "failed", "canceled"}:
            return payload
        time.sleep(0.02)
    raise AssertionError("job did not finish before timeout")


def _wait_for_batch(
    client: TestClient, batch_id: str, timeout_seconds: float = 10
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        payload = client.get(f"/api/v1/batches/{batch_id}").json()
        if payload["status"] in {
            "completed",
            "completed_with_errors",
            "failed",
            "canceled",
        }:
            return payload
        time.sleep(0.02)
    raise AssertionError("batch did not finish before timeout")
