from __future__ import annotations

from pathlib import Path

from docalign_core.config import Settings
from docalign_core.domain.formatting_spec import default_cleanup_spec
from fastapi.testclient import TestClient

from apps.api.db import RulePackVersionRecord
from apps.api.main import create_app


def test_rule_pack_versions_export_and_safe_restore(tmp_path: Path) -> None:
    data_dir = tmp_path / "rule-pack-api"
    settings = Settings(
        data_dir=data_dir,
        database_url=f"sqlite:///{data_dir / 'state.db'}",
    )
    original_spec = default_cleanup_spec().model_dump(mode="json")

    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/rule-packs").json() == {"rule_packs": []}

        created = client.post(
            "/api/v1/rule-packs",
            json={
                "request_id": "create-office-pack",
                "name": "办公室通用格式",
                "description": "由已确认样例整理，供内部报告复用。",
                "scope_label": "综合办公室内部报告",
                "spec": original_spec,
                "change_note": "从 2026 年确认样例建立",
            },
        )
        assert created.status_code == 201, created.text
        first = created.json()
        pack_id = first["pack_id"]
        assert first["schema_version"] == "rule-pack.v1"
        assert first["revision"] == 1
        assert first["approval_status"] == "draft"
        assert len(first["spec_sha256"]) == 64

        retried_create = client.post(
            "/api/v1/rule-packs",
            json={
                "request_id": "create-office-pack",
                "name": "办公室通用格式",
                "description": "由已确认样例整理，供内部报告复用。",
                "scope_label": "综合办公室内部报告",
                "spec": original_spec,
                "change_note": "从 2026 年确认样例建立",
            },
        )
        assert retried_create.status_code == 201
        assert retried_create.json()["pack_id"] == pack_id
        assert retried_create.json()["revision"] == 1

        duplicate = client.post(
            "/api/v1/rule-packs",
            json={
                "request_id": "duplicate-pack-name",
                "name": "  办公室通用格式  ",
                "scope_label": "重复名称",
                "spec": original_spec,
            },
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "RULE_PACK_NAME_CONFLICT"

        missing_approval_note = client.post(
            f"/api/v1/rule-packs/{pack_id}/versions",
            json={
                "request_id": "approve-without-note",
                "spec": original_spec,
                "change_note": "尝试确认",
                "approval_status": "locally_approved",
            },
        )
        assert missing_approval_note.status_code == 422

        changed_spec = default_cleanup_spec().model_copy(deep=True)
        assert changed_spec.baseline is not None
        assert changed_spec.baseline.font is not None
        changed_spec.baseline.font.size_pt = 11
        second = client.post(
            f"/api/v1/rule-packs/{pack_id}/versions",
            json={
                "request_id": "create-second-version",
                "spec": changed_spec.model_dump(mode="json"),
                "change_note": "正文调整为五号",
                "approval_status": "locally_approved",
                "approval_note": "张三依据 2026-08-29 内部模板逐项核对",
            },
        )
        assert second.status_code == 201, second.text
        second_payload = second.json()
        assert second_payload["revision"] == 2
        assert second_payload["approval_status"] == "locally_approved"
        assert second_payload["spec_sha256"] != first["spec_sha256"]

        retried_second = client.post(
            f"/api/v1/rule-packs/{pack_id}/versions",
            json={
                "request_id": "create-second-version",
                "spec": changed_spec.model_dump(mode="json"),
                "change_note": "正文调整为五号",
                "approval_status": "locally_approved",
                "approval_note": "张三依据 2026-08-29 内部模板逐项核对",
            },
        )
        assert retried_second.status_code == 201
        assert retried_second.json()["revision"] == 2

        reused_for_different_content = client.post(
            f"/api/v1/rule-packs/{pack_id}/versions",
            json={
                "request_id": "create-second-version",
                "spec": original_spec,
                "change_note": "另一项内容",
            },
        )
        assert reused_for_different_content.status_code == 409
        assert reused_for_different_content.json()["error"]["code"] == (
            "IDEMPOTENCY_KEY_REUSED"
        )

        catalog = client.get("/api/v1/rule-packs").json()["rule_packs"]
        assert len(catalog) == 1
        assert catalog[0]["current_revision"] == 2
        assert catalog[0]["current_approval_status"] == "locally_approved"

        detail = client.get(f"/api/v1/rule-packs/{pack_id}")
        assert detail.status_code == 200
        assert [item["revision"] for item in detail.json()["versions"]] == [2, 1]

        exported = client.get(f"/api/v1/rule-packs/{pack_id}/versions/1/export")
        assert exported.status_code == 200
        assert exported.headers["content-disposition"] == (
            f'attachment; filename="{pack_id}-r1.rule-pack.json"'
        )
        assert exported.json()["spec_sha256"] == first["spec_sha256"]

        restored = client.post(
            f"/api/v1/rule-packs/{pack_id}/restore",
            json={
                "request_id": "restore-first-version",
                "revision": 1,
                "change_note": "发现修订 2 不适用于旧版报告，恢复修订 1",
            },
        )
        assert restored.status_code == 201, restored.text
        restored_payload = restored.json()
        assert restored_payload["revision"] == 3
        assert restored_payload["restored_from_revision"] == 1
        assert restored_payload["approval_status"] == "draft"
        assert restored_payload["approval_note"] is None
        assert restored_payload["spec_sha256"] == first["spec_sha256"]
        assert client.get(f"/api/v1/rule-packs/{pack_id}").json()["current_revision"] == 3

        retried_restore = client.post(
            f"/api/v1/rule-packs/{pack_id}/restore",
            json={
                "request_id": "restore-first-version",
                "revision": 1,
                "change_note": "发现修订 2 不适用于旧版报告，恢复修订 1",
            },
        )
        assert retried_restore.status_code == 201
        assert retried_restore.json()["revision"] == 3


def test_rule_pack_integrity_failure_is_explicit(tmp_path: Path) -> None:
    data_dir = tmp_path / "rule-pack-integrity"
    app = create_app(
        Settings(
            data_dir=data_dir,
            database_url=f"sqlite:///{data_dir / 'state.db'}",
        )
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/rule-packs",
            json={
                "request_id": "create-integrity-pack",
                "name": "完整性检查",
                "scope_label": "测试范围",
                "spec": default_cleanup_spec().model_dump(mode="json"),
            },
        ).json()
        pack_id = created["pack_id"]
        database = app.state.database
        with database.session_factory.begin() as session:
            version = session.get(RulePackVersionRecord, (pack_id, 1))
            assert version is not None
            version.spec_sha256 = "0" * 64

        response = client.get(f"/api/v1/rule-packs/{pack_id}/versions/1")
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "RULE_PACK_INTEGRITY_FAILED"
