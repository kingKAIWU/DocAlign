from __future__ import annotations

import json
from pathlib import Path

from docalign_core.config import Settings
from docalign_core.domain.formatting_spec import default_cleanup_spec
from fastapi.testclient import TestClient

from apps.api.db import RulePackVersionRecord
from apps.api.main import create_app


def test_rule_pack_cross_machine_import_is_verified_deduplicated_and_downgraded(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source-machine"
    source_settings = Settings(
        data_dir=source_dir,
        database_url=f"sqlite:///{source_dir / 'state.db'}",
    )
    with TestClient(create_app(source_settings)) as source_client:
        created = source_client.post(
            "/api/v1/rule-packs",
            json={
                "request_id": "portable-source-pack",
                "name": "综合办公室正式月报",
                "description": "由办公室模板管理员逐项核对。",
                "scope_label": "2026 年综合办公室月报",
                "spec": default_cleanup_spec().model_dump(mode="json"),
                "change_note": "依据 2026-08-30 内部模板建立",
                "approval_status": "locally_approved",
                "approval_note": "王老师按内部模板逐项核对",
            },
        )
        assert created.status_code == 201, created.text
        source_artifact = created.json()
        exported = source_client.get(
            f"/api/v1/rule-packs/{source_artifact['pack_id']}/versions/1/export"
        )
        assert exported.status_code == 200
        portable_bytes = exported.content

        local_preview = source_client.post(
            "/api/v1/rule-packs/imports/preview",
            files={
                "file": (
                    "office.rule-pack.json",
                    portable_bytes,
                    "application/json",
                )
            },
        )
        assert local_preview.status_code == 200, local_preview.text
        assert local_preview.json()["already_present"] is True
        assert local_preview.json()["existing_pack_id"] == source_artifact["pack_id"]

    target_dir = tmp_path / "target-machine"
    target_settings = Settings(
        data_dir=target_dir,
        database_url=f"sqlite:///{target_dir / 'state.db'}",
    )
    target_app = create_app(target_settings)
    with TestClient(target_app) as target_client:
        preview = target_client.post(
            "/api/v1/rule-packs/imports/preview",
            files={
                "file": (
                    "office.rule-pack.json",
                    portable_bytes,
                    "application/json",
                )
            },
        )
        assert preview.status_code == 200, preview.text
        preview_payload = preview.json()
        assert preview_payload["integrity_verified"] is True
        assert preview_payload["signature_status"] == "unsigned"
        assert preview_payload["already_present"] is False
        assert preview_payload["source_name_conflict"] is False
        assert preview_payload["suggested_name"] == "综合办公室正式月报"
        assert preview_payload["source"]["approval_status"] == "locally_approved"
        assert preview_payload["source"]["spec_sha256"] == source_artifact["spec_sha256"]
        assert preview_payload["target_approval_status"] == "draft"
        assert target_client.get("/api/v1/rule-packs").json() == {"rule_packs": []}

        imported = target_client.post(
            "/api/v1/rule-packs/imports",
            data={
                "request_id": "import-office-pack",
                "name": preview_payload["suggested_name"],
            },
            files={
                "file": (
                    "office.rule-pack.json",
                    portable_bytes,
                    "application/json",
                )
            },
        )
        assert imported.status_code == 201, imported.text
        import_result = imported.json()
        artifact = import_result["artifact"]
        assert import_result["already_present"] is False
        assert artifact["pack_id"] != source_artifact["pack_id"]
        assert artifact["revision"] == 1
        assert artifact["approval_status"] == "draft"
        assert artifact["approval_note"] is None
        assert artifact["spec_sha256"] == source_artifact["spec_sha256"]
        assert artifact["import_source"] == preview_payload["source"]
        assert "需在本机重新核对" in artifact["change_note"]

        retried = target_client.post(
            "/api/v1/rule-packs/imports",
            data={
                "request_id": "import-office-pack",
                "name": preview_payload["suggested_name"],
            },
            files={
                "file": (
                    "office.rule-pack.json",
                    portable_bytes,
                    "application/json",
                )
            },
        )
        assert retried.status_code == 201
        assert retried.json()["already_present"] is False
        assert retried.json()["artifact"]["pack_id"] == artifact["pack_id"]

        deduplicated = target_client.post(
            "/api/v1/rule-packs/imports",
            data={
                "request_id": "import-office-again",
                "name": "另一个本地名称",
            },
            files={
                "file": (
                    "office.rule-pack.json",
                    portable_bytes,
                    "application/json",
                )
            },
        )
        assert deduplicated.status_code == 201
        assert deduplicated.json()["already_present"] is True
        assert deduplicated.json()["artifact"]["pack_id"] == artifact["pack_id"]
        assert len(target_client.get("/api/v1/rule-packs").json()["rule_packs"]) == 1

        detail = target_client.get(f"/api/v1/rule-packs/{artifact['pack_id']}").json()
        assert detail["versions"][0]["import_source"] == preview_payload["source"]
        reexported = target_client.get(
            f"/api/v1/rule-packs/{artifact['pack_id']}/versions/1/export"
        )
        assert reexported.json()["import_source"] == preview_payload["source"]

        with target_app.state.database.session_factory.begin() as session:
            version = session.get(RulePackVersionRecord, (artifact["pack_id"], 1))
            assert version is not None
            version.import_source_artifact_sha256 = "0" * 64
        corrupted = target_client.get(
            f"/api/v1/rule-packs/{artifact['pack_id']}/versions/1"
        )
        assert corrupted.status_code == 500
        assert corrupted.json()["error"]["code"] == "RULE_PACK_INTEGRITY_FAILED"


def test_rule_pack_import_rejects_tampering_unsafe_files_and_name_conflicts(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "import-validation-source"
    source_settings = Settings(
        data_dir=source_dir,
        database_url=f"sqlite:///{source_dir / 'state.db'}",
    )
    with TestClient(create_app(source_settings)) as source_client:
        source_artifact = source_client.post(
            "/api/v1/rule-packs",
            json={
                "request_id": "validation-source",
                "name": "冲突名称",
                "scope_label": "跨机导入校验",
                "spec": default_cleanup_spec().model_dump(mode="json"),
            },
        ).json()

    target_dir = tmp_path / "import-validation-target"
    target_settings = Settings(
        data_dir=target_dir,
        database_url=f"sqlite:///{target_dir / 'state.db'}",
        max_rule_pack_import_kb=64,
    )
    with TestClient(create_app(target_settings)) as target_client:
        target_client.post(
            "/api/v1/rule-packs",
            json={
                "request_id": "local-conflict-pack",
                "name": "冲突名称",
                "scope_label": "本机已有规则",
                "spec": default_cleanup_spec().model_dump(mode="json"),
            },
        )

        portable_bytes = json.dumps(source_artifact, ensure_ascii=False).encode()
        preview = target_client.post(
            "/api/v1/rule-packs/imports/preview",
            files={"file": ("conflict.json", portable_bytes, "application/json")},
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["source_name_conflict"] is True
        assert preview.json()["suggested_name"] == "冲突名称（导入）"

        conflicting = target_client.post(
            "/api/v1/rule-packs/imports",
            data={"request_id": "import-name-conflict", "name": "冲突名称"},
            files={"file": ("conflict.json", portable_bytes, "application/json")},
        )
        assert conflicting.status_code == 409
        assert conflicting.json()["error"]["code"] == "RULE_PACK_NAME_CONFLICT"

        tampered = json.loads(portable_bytes)
        tampered["spec"]["baseline"]["font"]["size_pt"] = 17
        tampered_response = target_client.post(
            "/api/v1/rule-packs/imports/preview",
            files={
                "file": (
                    "tampered.json",
                    json.dumps(tampered).encode(),
                    "application/json",
                )
            },
        )
        assert tampered_response.status_code == 422
        assert tampered_response.json()["error"]["code"] == (
            "RULE_PACK_IMPORT_INTEGRITY_FAILED"
        )

        malformed = target_client.post(
            "/api/v1/rule-packs/imports/preview",
            files={"file": ("malformed.json", b"{}", "application/json")},
        )
        assert malformed.status_code == 422
        assert malformed.json()["error"]["code"] == "RULE_PACK_IMPORT_INVALID"

        wrong_extension = target_client.post(
            "/api/v1/rule-packs/imports/preview",
            files={"file": ("rules.txt", portable_bytes, "text/plain")},
        )
        assert wrong_extension.status_code == 415
        assert wrong_extension.json()["error"]["code"] == (
            "RULE_PACK_IMPORT_UNSUPPORTED_FILE"
        )

        oversized = target_client.post(
            "/api/v1/rule-packs/imports/preview",
            files={
                "file": (
                    "oversized.json",
                    b"{" + b"x" * (64 * 1024),
                    "application/json",
                )
            },
        )
        assert oversized.status_code == 413
        assert oversized.json()["error"]["code"] == "RULE_PACK_IMPORT_TOO_LARGE"
        assert len(target_client.get("/api/v1/rule-packs").json()["rule_packs"]) == 1


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
