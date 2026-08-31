from __future__ import annotations

import io
import threading
import time
import zipfile
from pathlib import Path

import pytest
from docalign_core.config import Settings
from docalign_core.domain.formatting_spec import default_cleanup_spec
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from apps.api import service as service_module
from apps.api.db import (
    AnalysisRecord,
    BatchAttemptRecord,
    BatchItemRecord,
    BatchRecord,
    DocumentRecord,
    JobRecord,
    utcnow,
)
from apps.api.errors import ApiError
from apps.api.main import create_app

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def test_batch_is_idempotent_isolates_bad_files_and_packages_outputs(
    academic_docx: Path, tmp_path: Path
) -> None:
    data_dir = tmp_path / "batch-api"
    app = create_app(
        Settings(
            data_dir=data_dir,
            database_url=f"sqlite:///{data_dir / 'state.db'}",
            job_concurrency=2,
        )
    )

    with TestClient(app) as client:
        pack = _create_rule_pack(client)
        payload = _post_batch(client, academic_docx, pack["pack_id"])
        assert payload.status_code == 202, payload.text
        batch_id = payload.json()["batch_id"]

        batch = _wait_for_batch(client, batch_id)
        assert batch["status"] == "completed_with_errors", batch
        assert batch["summary"] == {
            "total": 2,
            "completed": 1,
            "failed": 1,
            "canceled": 0,
            "active": 0,
        }
        assert batch["progress"] == 100
        assert batch["processing_boundary_acknowledged"] is True
        completed = next(item for item in batch["items"] if item["status"] == "completed")
        failed = next(item for item in batch["items"] if item["status"] == "failed")
        assert completed["validation_passed"] is True
        assert completed["content_integrity_passed"] is True
        assert completed["attempt_count"] == 1
        assert completed["output_document_url"]
        assert failed["filename"] == "损坏文档.docx"
        assert failed["error_code"] == "INVALID_DOCX"
        assert failed["retryable"] is False

        audit = client.get(batch["audit_json_url"])
        assert audit.status_code == 200
        assert audit.json()["schema_version"] == "batch-audit.v2"
        assert audit.json()["processing_boundary_acknowledged"] is True
        assert "attachment" in audit.headers["content-disposition"]

        item_audit = client.get(completed["audit_json_url"])
        assert item_audit.status_code == 200
        acknowledgment = item_audit.json()[
            "source_processing_boundary_acknowledgment"
        ]
        assert acknowledgment["acknowledged"] is True
        assert acknowledgment["method"] == "explicit_batch"
        assert acknowledgment["acknowledged_at"] == batch["created_at"]

        packaged = client.get(batch["output_zip_url"])
        assert packaged.status_code == 200
        with zipfile.ZipFile(io.BytesIO(packaged.content)) as archive:
            names = archive.namelist()
            assert "batch-audit.json" in names
            assert any(name.endswith("_formatted.docx") for name in names)
            assert all("损坏文档" not in name for name in names)

        repeated = _post_batch(client, academic_docx, pack["pack_id"])
        assert repeated.status_code == 202
        assert repeated.json()["batch_id"] == batch_id
        with app.state.database.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(BatchRecord)) == 1
            assert session.scalar(select(func.count()).select_from(BatchAttemptRecord)) == 1

        reused = _post_batch(
            client,
            academic_docx,
            pack["pack_id"],
            batch_name="另一个批次",
        )
        assert reused.status_code == 409
        assert reused.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

        not_retryable = client.post(
            f"/api/v1/batches/{batch_id}/items/{failed['item_id']}/retry",
            json={"request_id": "retry-invalid-source"},
        )
        assert not_retryable.status_code == 409
        assert not_retryable.json()["error"]["code"] == "BATCH_ITEM_NOT_RETRYABLE"


def test_batch_requires_explicit_complex_content_review_policy(
    academic_docx: Path, tmp_path: Path
) -> None:
    data_dir = tmp_path / "batch-acknowledgment"
    app = create_app(
        Settings(data_dir=data_dir, database_url=f"sqlite:///{data_dir / 'state.db'}")
    )
    with TestClient(app) as client:
        pack = _create_rule_pack(client, request_id="batch-acknowledgment-pack")
        response = client.post(
            "/api/v1/batches",
            data={
                "request_id": "batch-acknowledgment-missing",
                "name": "未确认批次",
                "rule_pack_id": str(pack["pack_id"]),
                "rule_pack_revision": "1",
                "processing_boundary_acknowledged": "false",
            },
            files=[
                ("files", ("待处理.docx", academic_docx.read_bytes(), DOCX_MEDIA_TYPE))
            ],
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == (
            "BATCH_PROCESSING_BOUNDARY_ACKNOWLEDGMENT_REQUIRED"
        )
        with app.state.database.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(BatchRecord)) == 0


def test_failed_batch_item_retries_with_attempt_history(
    academic_docx: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "batch-retry"
    app = create_app(Settings(data_dir=data_dir, database_url=f"sqlite:///{data_dir / 'state.db'}"))
    with TestClient(app) as client:
        pack = _create_rule_pack(client, request_id="create-retry-pack")
        with academic_docx.open("rb") as source:
            created = client.post(
                "/api/v1/batches",
                data={
                    "request_id": "create-retry-batch",
                    "name": "重试验证",
                    "rule_pack_id": pack["pack_id"],
                    "rule_pack_revision": "1",
                    "processing_boundary_acknowledged": "true",
                },
                files=[("files", ("待重试.docx", source, DOCX_MEDIA_TYPE))],
            )
        assert created.status_code == 202, created.text
        batch = _wait_for_batch(client, created.json()["batch_id"])
        item = batch["items"][0]
        assert item["status"] == "completed"

        with app.state.database.session_factory.begin() as session:
            job = session.get(JobRecord, item["job_id"])
            assert job is not None
            job.status = "failed"
            job.progress = 75
            job.error_code = "JOB_INTERRUPTED"
            job.error_message = "Simulated restart."
            job.updated_at = utcnow()

        interrupted = client.get(f"/api/v1/batches/{batch['batch_id']}").json()
        assert interrupted["status"] == "failed"
        assert interrupted["items"][0]["retryable"] is True

        original_create_job = app.state.service.create_job
        create_calls = 0

        def fail_once(document_id: str, analysis_id: str, spec_id: str, **kwargs: object):
            nonlocal create_calls
            create_calls += 1
            if create_calls == 1:
                raise ApiError(503, "TEMPORARY_PREPARATION_FAILURE", "Try again.")
            return original_create_job(document_id, analysis_id, spec_id, **kwargs)

        monkeypatch.setattr(app.state.service, "create_job", fail_once)
        preparation_failure = client.post(
            f"/api/v1/batches/{batch['batch_id']}/items/{item['item_id']}/retry",
            json={"request_id": "retry-preparation-failure"},
        )
        assert preparation_failure.status_code == 503
        after_preparation_failure = client.get(f"/api/v1/batches/{batch['batch_id']}").json()
        assert after_preparation_failure["items"][0]["attempt_count"] == 2
        assert after_preparation_failure["items"][0]["status"] == "failed"

        retried = client.post(
            f"/api/v1/batches/{batch['batch_id']}/items/{item['item_id']}/retry",
            json={"request_id": "retry-after-restart"},
        )
        assert retried.status_code == 202, retried.text
        recovered = _wait_for_batch(client, batch["batch_id"])
        assert recovered["status"] == "completed"
        assert recovered["items"][0]["attempt_count"] == 3
        assert recovered["items"][0]["job_id"] != item["job_id"]

        repeated = client.post(
            f"/api/v1/batches/{batch['batch_id']}/items/{item['item_id']}/retry",
            json={"request_id": "retry-after-restart"},
        )
        assert repeated.status_code == 202
        assert repeated.json()["items"][0]["attempt_count"] == 3
        with app.state.database.session_factory() as session:
            attempts = list(
                session.scalars(
                    select(BatchAttemptRecord)
                    .where(BatchAttemptRecord.batch_item_id == item["item_id"])
                    .order_by(BatchAttemptRecord.attempt_number)
                )
            )
        assert [attempt.attempt_number for attempt in attempts] == [1, 2, 3]
        assert attempts[0].job_id == item["job_id"]
        assert attempts[1].job_id is None
        assert attempts[2].job_id == recovered["items"][0]["job_id"]


def test_batch_cancel_is_cooperative_idempotent_and_delete_cleans_local_data(
    academic_docx: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "batch-lifecycle"
    app = create_app(
        Settings(
            data_dir=data_dir,
            database_url=f"sqlite:///{data_dir / 'state.db'}",
            job_concurrency=1,
        )
    )
    started = threading.Event()
    release = threading.Event()
    process_calls = 0
    original_process = service_module.process_document

    def blocking_process(*args, **kwargs):
        nonlocal process_calls
        process_calls += 1
        if process_calls == 2:
            started.set()
            if not release.wait(5):
                raise AssertionError("test did not release the blocked processing stage")
        return original_process(*args, **kwargs)

    monkeypatch.setattr(service_module, "process_document", blocking_process)

    with TestClient(app) as client:
        pack = _create_rule_pack(client, request_id="create-lifecycle-pack")
        created = client.post(
            "/api/v1/batches",
            data={
                "request_id": "create-lifecycle-batch",
                "name": "待取消批次",
                "rule_pack_id": str(pack["pack_id"]),
                "rule_pack_revision": "1",
                "processing_boundary_acknowledged": "true",
            },
            files=[
                ("files", ("运行中.docx", academic_docx.read_bytes(), DOCX_MEDIA_TYPE)),
                ("files", ("排队中.docx", academic_docx.read_bytes(), DOCX_MEDIA_TYPE)),
                ("files", ("仍在排队.docx", academic_docx.read_bytes(), DOCX_MEDIA_TYPE)),
            ],
        )
        assert created.status_code == 202, created.text
        batch_id = created.json()["batch_id"]
        assert started.wait(5)

        active_delete = client.delete(f"/api/v1/batches/{batch_id}")
        assert active_delete.status_code == 409
        assert active_delete.json()["error"]["code"] == "BATCH_NOT_TERMINAL"

        try:
            canceled = client.post(f"/api/v1/batches/{batch_id}/cancel")
            assert canceled.status_code == 202, canceled.text
            assert canceled.json()["status"] == "canceling"
            statuses = {item["status"] for item in canceled.json()["items"]}
            assert statuses == {"completed", "canceling", "canceled"}

            repeated = client.post(f"/api/v1/batches/{batch_id}/cancel")
            assert repeated.status_code == 202
            assert repeated.json()["status"] == "canceling"
        finally:
            release.set()

        terminal = _wait_for_batch(client, batch_id)
        assert terminal["status"] == "canceled"
        assert terminal["summary"] == {
            "total": 3,
            "completed": 1,
            "failed": 0,
            "canceled": 2,
            "active": 0,
        }
        assert [item["status"] for item in terminal["items"]] == [
            "completed",
            "canceled",
            "canceled",
        ]
        assert all(item["retryable"] is False for item in terminal["items"])
        assert terminal["output_zip_url"]
        packaged = client.get(terminal["output_zip_url"])
        assert packaged.status_code == 200
        with zipfile.ZipFile(io.BytesIO(packaged.content)) as archive:
            output_names = [name for name in archive.namelist() if name.endswith("_formatted.docx")]
            assert len(output_names) == 1
        assert process_calls == 2

        document_ids = [item["document_id"] for item in terminal["items"]]
        job_ids = [item["job_id"] for item in terminal["items"]]
        assert all(document_ids)
        assert all(job_ids)
        for item in terminal["items"]:
            if item["status"] == "canceled":
                assert not (data_dir / "outputs" / str(item["job_id"])).exists()
                assert not (data_dir / "jobs" / str(item["job_id"])).exists()

        final_cancel = client.post(f"/api/v1/batches/{batch_id}/cancel")
        assert final_cancel.status_code == 202
        assert final_cancel.json()["status"] == "canceled"

        deleted = client.delete(f"/api/v1/batches/{batch_id}")
        assert deleted.status_code == 204
        assert client.get(f"/api/v1/batches/{batch_id}").status_code == 404
        assert not (data_dir / "batches" / batch_id).exists()
        assert all(
            not (data_dir / "uploads" / str(document_id)).exists() for document_id in document_ids
        )
        assert all(not (data_dir / "outputs" / str(job_id)).exists() for job_id in job_ids)
        assert all(not (data_dir / "jobs" / str(job_id)).exists() for job_id in job_ids)
        assert client.get(f"/api/v1/rule-packs/{pack['pack_id']}").status_code == 200
        with app.state.database.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(BatchRecord)) == 0
            assert session.scalar(select(func.count()).select_from(DocumentRecord)) == 0
            assert session.scalar(select(func.count()).select_from(AnalysisRecord)) == 0
            assert session.scalar(select(func.count()).select_from(JobRecord)) == 0


def test_batch_cancel_closes_a_retry_preparation_reservation(
    academic_docx: Path, tmp_path: Path
) -> None:
    data_dir = tmp_path / "batch-cancel-reservation"
    app = create_app(Settings(data_dir=data_dir, database_url=f"sqlite:///{data_dir / 'state.db'}"))
    with TestClient(app) as client:
        pack = _create_rule_pack(client, request_id="create-reservation-pack")
        with academic_docx.open("rb") as source:
            created = client.post(
                "/api/v1/batches",
                data={
                    "request_id": "create-reservation-batch",
                    "name": "取消重试准备",
                    "rule_pack_id": str(pack["pack_id"]),
                    "rule_pack_revision": "1",
                    "processing_boundary_acknowledged": "true",
                },
                files=[("files", ("待重试.docx", source, DOCX_MEDIA_TYPE))],
            )
        terminal = _wait_for_batch(client, created.json()["batch_id"])
        item = terminal["items"][0]
        with app.state.database.session_factory.begin() as session:
            job = session.get(JobRecord, item["job_id"])
            reservation = session.get(BatchItemRecord, item["item_id"])
            assert job is not None and reservation is not None
            job.status = "failed"
            job.error_code = "JOB_INTERRUPTED"
            reservation.status = "preparing"

        canceled = client.post(f"/api/v1/batches/{terminal['batch_id']}/cancel")
        assert canceled.status_code == 202
        assert canceled.json()["status"] == "canceled"
        assert canceled.json()["items"][0]["status"] == "canceled"
        retry = client.post(
            f"/api/v1/batches/{terminal['batch_id']}/items/{item['item_id']}/retry",
            json={"request_id": "retry-after-batch-cancel"},
        )
        assert retry.status_code == 409
        assert retry.json()["error"]["code"] == "BATCH_CANCELED"


def _create_rule_pack(
    client: TestClient, request_id: str = "create-batch-pack"
) -> dict[str, object]:
    response = client.post(
        "/api/v1/rule-packs",
        json={
            "request_id": request_id,
            "name": f"批处理规则-{request_id}",
            "scope_label": "批量验证文档",
            "spec": default_cleanup_spec().model_dump(mode="json"),
            "approval_status": "locally_approved",
            "approval_note": "测试夹具已逐项核对",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _post_batch(
    client: TestClient,
    source_path: Path,
    pack_id: object,
    *,
    batch_name: str = "月度材料",
):
    return client.post(
        "/api/v1/batches",
        data={
            "request_id": "create-monthly-batch",
            "name": batch_name,
            "rule_pack_id": str(pack_id),
            "rule_pack_revision": "1",
            "processing_boundary_acknowledged": "true",
        },
        files=[
            ("files", ("合格文档.docx", source_path.read_bytes(), DOCX_MEDIA_TYPE)),
            ("files", ("损坏文档.docx", b"not-a-docx", DOCX_MEDIA_TYPE)),
        ],
    )


def _wait_for_batch(
    client: TestClient, batch_id: str, timeout_seconds: float = 10
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/batches/{batch_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in {
            "completed",
            "completed_with_errors",
            "failed",
            "canceled",
        }:
            return payload
        time.sleep(0.02)
    raise AssertionError("batch did not finish before timeout")
