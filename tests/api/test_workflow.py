from __future__ import annotations

import time
from pathlib import Path

import pytest
from docalign_core.analysis.semantic import SemanticAnalysisDraft, SemanticRoleAssignment
from docalign_core.config import Settings
from docalign_core.domain.document_ir import AnalysisResult, DocumentIR, ParagraphIR
from docalign_core.domain.enums import DocumentKind, SemanticRole
from docalign_core.domain.formatting_spec import (
    FontSpec,
    FormattingSpec,
    RoleFormattingSpec,
    default_academic_spec,
    default_cleanup_spec,
)
from docalign_core.llm.base import (
    DocumentSummary,
    RequirementCompilationError,
    RequirementCompilationResult,
)
from fastapi.testclient import TestClient

from apps.api import service as service_module
from apps.api.main import create_app


def test_complete_structured_api_workflow(academic_docx: Path, tmp_path: Path) -> None:
    data_dir = tmp_path / "api-data"
    settings = Settings(
        data_dir=data_dir,
        database_url=f"sqlite:///{data_dir / 'test.db'}",
        llm_base_url="",
        llm_model="",
    )
    with TestClient(create_app(settings)) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        capabilities = client.get("/api/v1/capabilities").json()
        assert capabilities["llm_configured"] is False
        assert capabilities["auto_layout"] is True
        assert capabilities["audit_only"] is True
        assert capabilities["format_manifest"] is True
        assert capabilities["template_rule_candidate"] is True

        with academic_docx.open("rb") as reference:
            template_candidate = client.post(
                "/api/v1/templates/candidate",
                files={
                    "file": (
                        "approved-reference.docx",
                        reference,
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
            )
        assert template_candidate.status_code == 200, template_candidate.text
        candidate_payload = template_candidate.json()
        assert candidate_payload["schema_version"] == "template-rule-candidate.v1"
        assert candidate_payload["source_filename"] == "approved-reference.docx"
        assert candidate_payload["spec"]["source"]["type"] == "template"
        assert candidate_payload["warnings"]
        assert not list((data_dir / "uploads").glob("template_*"))

        with academic_docx.open("rb") as source:
            upload = client.post(
                "/api/v1/documents",
                files={
                    "file": (
                        "academic.docx",
                        source,
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
            )
        assert upload.status_code == 201, upload.text
        document_id = upload.json()["document_id"]
        source = client.get(f"/api/v1/documents/{document_id}/source")
        assert source.status_code == 200
        assert source.content == academic_docx.read_bytes()
        preset = client.get("/api/v1/presets/generic-academic-cn")
        assert preset.status_code == 200
        assert preset.json()["preset_id"] == "generic-academic-cn"
        cleanup_preset = client.get("/api/v1/presets/default-clean-cn")
        assert cleanup_preset.status_code == 200
        assert cleanup_preset.json()["preset_id"] == "default-clean-cn"
        assert cleanup_preset.json()["spec"] == default_cleanup_spec().model_dump(mode="json")
        preset_catalog = client.get("/api/v1/presets")
        assert preset_catalog.status_code == 200
        preset_items = preset_catalog.json()["presets"]
        assert [item["preset_id"] for item in preset_items] == [
            "default-clean-cn",
            "compact-clean-cn",
            "contract-clean-cn",
            "wide-table-clean-cn",
            "gbt-9704-2012-body-reference-cn",
            "nankai-thesis-2026-reference-cn",
            "bigc-master-thesis-2025-reference-cn",
        ]
        assert all(item["metadata"]["claim_level"] == "generic" for item in preset_items[:4])
        assert all(item["metadata"]["claim_level"] == "reference" for item in preset_items[4:])
        assert all(item["metadata"]["pack_version"] == "1.0.0" for item in preset_items)
        assert all(item["metadata"]["limitations"] for item in preset_items)
        assert all(item["metadata"]["coverage_items"] for item in preset_items[4:])
        assert all(item["metadata"]["acceptance_evidence"] for item in preset_items[4:])

        analysis_response = client.post(f"/api/v1/documents/{document_id}/analyze")
        assert analysis_response.status_code == 201, analysis_response.text
        analysis = analysis_response.json()
        analysis_id = analysis["analysis_id"]
        assert client.get(f"/api/v1/analyses/{analysis_id}").status_code == 200
        smart_disabled = client.post(
            f"/api/v1/documents/{document_id}/analyze", json={"mode": "smart"}
        )
        assert smart_disabled.status_code == 503
        assert smart_disabled.json()["error"]["code"] == "SEMANTIC_ANALYSIS_NOT_CONFIGURED"
        body_node = next(
            block["node_id"]
            for block in analysis["document_ir"]["blocks"]
            if block["kind"] == "paragraph" and "正文中文" in block["text"]
        )
        override = client.put(
            f"/api/v1/analyses/{analysis_id}/role-overrides",
            json={"overrides": [{"node_id": body_node, "role": "body"}]},
        )
        assert override.status_code == 200
        invalid_override = client.put(
            f"/api/v1/analyses/{analysis_id}/role-overrides",
            json={"overrides": [{"node_id": "missing-node", "role": "body"}]},
        )
        assert invalid_override.status_code == 422
        assert invalid_override.json()["error"]["code"] == "INVALID_ROLE_OVERRIDE"

        spec_payload = default_academic_spec().model_dump(mode="json")
        spec_response = client.post(
            "/api/v1/specs",
            json={
                "document_id": document_id,
                "spec": spec_payload,
            },
        )
        assert spec_response.status_code == 201, spec_response.text
        spec_id = spec_response.json()["spec_id"]
        assert client.get(f"/api/v1/specs/{spec_id}").status_code == 200
        assert (
            client.put(
                f"/api/v1/specs/{spec_id}",
                json={"document_id": document_id, "spec": spec_payload},
            ).status_code
            == 200
        )
        assert client.post("/api/v1/specs/validate", json={"spec": spec_payload}).json()["valid"]

        manifest = client.get(f"/api/v1/documents/{document_id}/format-manifest")
        assert manifest.status_code == 200, manifest.text
        assert manifest.headers["content-disposition"] == (
            'attachment; filename="format-manifest.json"'
        )
        manifest_payload = manifest.json()
        assert manifest_payload["schema_version"] == "format-manifest.v1"
        assert manifest_payload["source_filename"] == "academic.docx"
        assert manifest_payload["summary"]["requirement_count"] > 0
        assert manifest_payload["requirements"][0]["requirement_id"] == "R0001"

        compliance = client.post(
            f"/api/v1/documents/{document_id}/compliance",
            json={"analysis_id": analysis_id, "spec_id": spec_id},
        )
        assert compliance.status_code == 200, compliance.text
        compliance_payload = compliance.json()
        assert compliance_payload["schema_version"] == "compliance-report.v1"
        assert compliance_payload["document_id"] == document_id
        assert compliance_payload["summary"]["total_violations"] > 0
        assert any(item["locator"] for item in compliance_payload["violations"])
        llm_disabled = client.post(
            "/api/v1/specs/compile",
            json={
                "document_id": document_id,
                "analysis_id": analysis_id,
                "instruction": "正文宋体",
            },
        )
        assert llm_disabled.status_code == 503
        assert llm_disabled.json()["error"]["code"] == "LLM_NOT_CONFIGURED"

        job_response = client.post(
            "/api/v1/jobs",
            json={
                "document_id": document_id,
                "analysis_id": analysis_id,
                "spec_id": spec_id,
            },
        )
        assert job_response.status_code == 202, job_response.text
        job_id = job_response.json()["job_id"]
        job = _wait_for_job(client, job_id)
        assert job["status"] == "completed", job
        assert job["auto_layout_splits"] == 0
        result_summary = job["result_summary"]
        assert result_summary["validation_passed"] is True
        assert result_summary["content_integrity_passed"] is True
        assert result_summary["structure_review_items"] == result_summary["remaining_review_items"]
        assert result_summary["delivery_review_items"] == 0
        execution_evidence = result_summary["execution_evidence"]
        assert execution_evidence["engine_version"] == "0.1.0"
        assert len(execution_evidence["spec_sha256"]) == 64
        assert execution_evidence["applied_preset"] is None
        assert result_summary["changed_mutations"] > 0
        assert (
            sum(result_summary["change_categories"].values()) == result_summary["changed_mutations"]
        )
        assert 0 < len(result_summary["change_details"]) <= 32
        assert isinstance(result_summary["change_details_truncated"], bool)
        assert any(item["locator"] for item in result_summary["change_details"])
        assert all(item["property_path"] for item in result_summary["change_details"])
        assert all(
            item["before_value"] != item["after_value"] for item in result_summary["change_details"]
        )
        assert any(
            item["property_path"] == "section.layout" and "mm" in (item["after_value"] or "")
            for item in result_summary["change_details"]
        )
        assert any(
            item["property_path"].startswith("runs.") and "中文字体" in (item["after_value"] or "")
            for item in result_summary["change_details"]
        )

        output = client.get(f"/api/v1/jobs/{job_id}/output")
        assert output.status_code == 200
        assert output.content.startswith(b"PK")
        audit = client.get(f"/api/v1/jobs/{job_id}/audit.json")
        assert audit.status_code == 200
        audit_payload = audit.json()
        assert audit_payload["validation"]["valid"] is True
        assert (
            result_summary["remaining_review_items"] == audit_payload["summary"]["unknown_blocks"]
        )
        audit_markdown = client.get(f"/api/v1/jobs/{job_id}/audit.md")
        assert audit_markdown.status_code == 200
        assert "DocAlign 格式化审计" in audit_markdown.text
        assert "## 实际格式变更" in audit_markdown.text
        assert "paragraph.style" in audit_markdown.text

        deleted = client.delete(f"/api/v1/documents/{document_id}")
        assert deleted.status_code == 204
        assert client.get(f"/api/v1/documents/{document_id}").status_code == 404


def test_invalid_upload_uses_stable_error_envelope(tmp_path: Path) -> None:
    data_dir = tmp_path / "api-invalid"
    settings = Settings(
        data_dir=data_dir,
        database_url=f"sqlite:///{data_dir / 'test.db'}",
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/documents",
            files={"file": ("bad.docx", b"not a zip", "application/octet-stream")},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_DOCX"
        validation = client.post("/api/v1/jobs", json={})
        assert validation.status_code == 422
        assert validation.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"


def test_plain_text_document_enters_the_same_analysis_and_formatting_pipeline(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "api-text"
    settings = Settings(
        data_dir=data_dir,
        database_url=f"sqlite:///{data_dir / 'test.db'}",
    )
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/v1/documents/from-text",
            json={
                "filename": "研究草稿.docx",
                "text": "# 智能排版研究\n## 背景\n这是正文。\n- 结构识别",
            },
        )
        assert created.status_code == 201, created.text
        document_id = created.json()["document_id"]
        analysis = client.post(f"/api/v1/documents/{document_id}/analyze")
        assert analysis.status_code == 201, analysis.text
        assert analysis.json()["summary"]["role_counts"]["title"] == 1
        source = client.get(f"/api/v1/documents/{document_id}/source")
        assert source.status_code == 200
        assert source.content.startswith(b"PK")

        spec_id = client.post(
            "/api/v1/specs",
            json={
                "document_id": document_id,
                "spec": default_academic_spec().model_dump(mode="json"),
            },
        ).json()["spec_id"]
        job = client.post(
            "/api/v1/jobs",
            json={
                "document_id": document_id,
                "analysis_id": analysis.json()["analysis_id"],
                "spec_id": spec_id,
            },
        )
        completed = _wait_for_job(client, job.json()["job_id"])
        assert completed["status"] == "completed"


def test_job_rejects_source_changed_after_analysis(academic_docx: Path, tmp_path: Path) -> None:
    data_dir = tmp_path / "api-mismatch"
    settings = Settings(
        data_dir=data_dir,
        database_url=f"sqlite:///{data_dir / 'test.db'}",
    )
    with TestClient(create_app(settings)) as client:
        with academic_docx.open("rb") as source:
            document = client.post(
                "/api/v1/documents",
                files={"file": ("academic.docx", source, "application/octet-stream")},
            ).json()
        document_id = document["document_id"]
        analysis_id = client.post(f"/api/v1/documents/{document_id}/analyze").json()["analysis_id"]
        spec_id = client.post(
            "/api/v1/specs",
            json={
                "document_id": document_id,
                "spec": default_academic_spec().model_dump(mode="json"),
            },
        ).json()["spec_id"]
        record = client.app.state.service.get_document(document_id)
        Path(record.stored_path).write_bytes(b"changed after analysis")

        response = client.post(
            "/api/v1/jobs",
            json={
                "document_id": document_id,
                "analysis_id": analysis_id,
                "spec_id": spec_id,
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "ANALYSIS_SOURCE_MISMATCH"


def test_configured_llm_compile_uses_only_document_summary(
    academic_docx: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summaries: list[DocumentSummary | None] = []

    class FakeInterpreter:
        def __init__(self, **_: object) -> None:
            pass

        async def compile_requirements(
            self, instruction: str, document_summary: DocumentSummary | None = None
        ) -> RequirementCompilationResult:
            if instruction == "fail":
                raise RequirementCompilationError("LLM_TIMEOUT", "timed out")
            summaries.append(document_summary)
            return RequirementCompilationResult(
                spec=FormattingSpec(
                    roles={
                        SemanticRole.BODY: RoleFormattingSpec(
                            font=FontSpec(east_asia="宋体", ascii="Times New Roman", size_pt=12)
                        )
                    }
                ),
                provider="mock",
                model="mock-model",
            )

    monkeypatch.setattr(service_module, "OpenAICompatibleChatInterpreter", FakeInterpreter)
    data_dir = tmp_path / "api-llm"
    settings = Settings(
        data_dir=data_dir,
        database_url=f"sqlite:///{data_dir / 'test.db'}",
        llm_base_url="https://example.test/v1",
        llm_model="mock-model",
    )
    with TestClient(create_app(settings)) as client:
        with academic_docx.open("rb") as source:
            upload = client.post(
                "/api/v1/documents",
                files={"file": ("academic.docx", source, "application/octet-stream")},
            )
        document_id = upload.json()["document_id"]
        analysis_id = client.post(f"/api/v1/documents/{document_id}/analyze").json()["analysis_id"]
        compiled = client.post(
            "/api/v1/specs/compile",
            json={
                "document_id": document_id,
                "analysis_id": analysis_id,
                "instruction": "正文宋体小四",
            },
        )
        assert compiled.status_code == 201, compiled.text
        assert compiled.json()["spec_id"].startswith("spec_")
        assert compiled.json()["spec"]["document"] is None
        assert set(compiled.json()["spec"]["roles"]) == {"body"}
        assert summaries and summaries[0] is not None
        assert summaries[0].paragraph_count > 0

        intelligent = client.post(
            "/api/v1/specs/compile",
            json={
                "document_id": document_id,
                "analysis_id": analysis_id,
                "instruction": "正文宋体小四",
                "apply_preset": True,
            },
        )
        assert intelligent.status_code == 201, intelligent.text
        intelligent_spec = intelligent.json()["spec"]
        assert intelligent_spec["document"] is not None
        assert "title" in intelligent_spec["roles"]
        assert "list_item" in intelligent_spec["roles"]
        assert intelligent_spec["roles"]["body"]["font"]["east_asia"] == "宋体"

        failed = client.post(
            "/api/v1/specs/compile",
            json={"document_id": document_id, "instruction": "fail"},
        )
        assert failed.status_code == 502
        assert failed.json()["error"]["code"] == "LLM_TIMEOUT"


def test_smart_analysis_reviews_plain_semantics_with_configured_model(
    academic_docx: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeSemanticAnalyzer:
        provider = "mock-semantic"
        model = "semantic-model"

        def __init__(self, **_: object) -> None:
            pass

        async def analyze(
            self, document_ir: DocumentIR, deterministic: AnalysisResult
        ) -> SemanticAnalysisDraft:
            candidate = next(
                block
                for block in deterministic.document_ir.blocks
                if isinstance(block, ParagraphIR) and block.detected_role == SemanticRole.BODY
            )
            return SemanticAnalysisDraft(
                document_kind=DocumentKind.ACADEMIC_PAPER,
                document_kind_confidence=0.93,
                assignments=[
                    SemanticRoleAssignment(
                        node_id=candidate.node_id,
                        role=SemanticRole.HEADING_2,
                        confidence=0.88,
                        evidence="semantic section label",
                    )
                ],
            )

    monkeypatch.setattr(service_module, "OpenAICompatibleSemanticAnalyzer", FakeSemanticAnalyzer)
    data_dir = tmp_path / "api-smart-analysis"
    settings = Settings(
        data_dir=data_dir,
        database_url=f"sqlite:///{data_dir / 'test.db'}",
        llm_base_url="https://example.test/v1",
        llm_model="semantic-model",
    )
    with TestClient(create_app(settings)) as client:
        with academic_docx.open("rb") as source:
            document_id = client.post(
                "/api/v1/documents",
                files={"file": ("academic.docx", source, "application/octet-stream")},
            ).json()["document_id"]
        response = client.post(
            f"/api/v1/documents/{document_id}/analyze",
            json={"mode": "smart"},
        )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["summary"]["analysis_mode"] == "smart"
    assert payload["summary"]["document_kind"] == "academic_paper"
    assert payload["summary"]["model_provider"] == "mock-semantic"
    assert any(
        block.get("role_source") == "llm"
        for block in payload["document_ir"]["blocks"]
        if block["kind"] == "paragraph"
    )


def _wait_for_job(client: TestClient, job_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        payload = client.get(f"/api/v1/jobs/{job_id}").json()
        if payload["status"] in {"completed", "failed"}:
            return payload
        time.sleep(0.05)
    raise AssertionError("job did not finish before the test timeout")
