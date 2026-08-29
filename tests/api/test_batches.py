from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

import pytest
from docalign_core.config import Settings
from docalign_core.domain.formatting_spec import default_cleanup_spec
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from apps.api.db import BatchAttemptRecord, BatchRecord, JobRecord, utcnow
from apps.api.errors import ApiError
from apps.api.main import create_app

DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


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
            "active": 0,
        }
        assert batch["progress"] == 100
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
        assert audit.json()["schema_version"] == "batch-audit.v1"
        assert "attachment" in audit.headers["content-disposition"]

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


def test_failed_batch_item_retries_with_attempt_history(
    academic_docx: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "batch-retry"
    app = create_app(
        Settings(data_dir=data_dir, database_url=f"sqlite:///{data_dir / 'state.db'}")
    )
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

        def fail_once(document_id: str, analysis_id: str, spec_id: str):
            nonlocal create_calls
            create_calls += 1
            if create_calls == 1:
                raise ApiError(503, "TEMPORARY_PREPARATION_FAILURE", "Try again.")
            return original_create_job(document_id, analysis_id, spec_id)

        monkeypatch.setattr(app.state.service, "create_job", fail_once)
        preparation_failure = client.post(
            f"/api/v1/batches/{batch['batch_id']}/items/{item['item_id']}/retry",
            json={"request_id": "retry-preparation-failure"},
        )
        assert preparation_failure.status_code == 503
        after_preparation_failure = client.get(
            f"/api/v1/batches/{batch['batch_id']}"
        ).json()
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
        if payload["status"] in {"completed", "completed_with_errors", "failed"}:
            return payload
        time.sleep(0.02)
    raise AssertionError("batch did not finish before timeout")
