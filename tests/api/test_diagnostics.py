from __future__ import annotations

import json
from pathlib import Path

from docalign_core.config import Settings
from docalign_core.domain.formatting_spec import default_academic_spec
from fastapi.testclient import TestClient

from apps.api.db import Database, DocumentRecord, JobRecord
from apps.api.diagnostics import DiagnosticService, standalone_diagnostic_service
from apps.api.main import create_app
from apps.api.storage import LocalStorage


def test_support_diagnostic_is_actionable_and_excludes_private_values(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "private-workspace"
    database_url = f"sqlite:///{data_dir / 'private-state.db'}"
    settings = Settings(
        data_dir=data_dir,
        database_url=database_url,
        llm_base_url="https://private-model.example/v1",
        llm_api_key="credential-leak-sentinel",
        llm_model="private-model-name",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        source = Path("tests/fixtures/academic-comprehensive.docx")
        with source.open("rb") as stream:
            document = client.post(
                "/api/v1/documents",
                files={
                    "file": (
                        "private-person-name.docx",
                        stream,
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
            ).json()
        analysis = client.post(
            f"/api/v1/documents/{document['document_id']}/analyze",
            json={"mode": "deterministic"},
        ).json()
        spec = client.post(
            "/api/v1/specs",
            json={
                "document_id": document["document_id"],
                "spec": default_academic_spec().model_dump(mode="json"),
            },
        ).json()
        with app.state.database.session_factory.begin() as session:
            session.add(
                JobRecord(
                    id="job_private_identifier",
                    document_id=document["document_id"],
                    analysis_id=analysis["analysis_id"],
                    spec_id=spec["spec_id"],
                    status="failed",
                    progress=0,
                    error_code="UNSAFE\nprivate-person-name",
                    error_message="credential-leak-sentinel private failure details",
                )
            )

        response = client.get("/api/v1/diagnostics")
        assert response.status_code == 200
        payload = response.json()
        assert payload["schema_version"] == "support-diagnostic.v1"
        assert payload["overall"] == "ready"
        assert {check["check_id"] for check in payload["checks"]} == {
            "database_connection",
            "database_schema",
            "data_directory",
            "disk_capacity",
            "artifact_references",
            "optional_model",
        }
        assert payload["data_summary"]["documents"] == 1
        assert payload["data_summary"]["analyses"] == 1
        assert payload["data_summary"]["jobs"] == 1
        assert payload["data_summary"]["failed_jobs"] == 1
        assert payload["recent_error_codes"] == [
            {"code": "UNKNOWN_ERROR", "count": 1}
        ]
        assert payload["excluded_data"] == [
            "document_content",
            "filenames",
            "record_identifiers",
            "local_paths",
            "database_connection_string",
            "model_endpoint",
            "credentials",
            "raw_logs",
        ]

        serialized = json.dumps(payload, ensure_ascii=False)
        for private_value in (
            "private-person-name.docx",
            document["document_id"],
            analysis["analysis_id"],
            "job_private_identifier",
            str(data_dir),
            database_url,
            "https://private-model.example/v1",
            "credential-leak-sentinel",
            "private-model-name",
            "private failure details",
        ):
            assert private_value not in serialized

        exported = client.get("/api/v1/diagnostics/export")
        assert exported.status_code == 200
        assert exported.json()["schema_version"] == "support-diagnostic.v1"
        assert exported.headers["content-disposition"] == (
            'attachment; filename="docalign-support-diagnostic.json"'
        )


def test_standalone_diagnostic_does_not_create_missing_workspace(tmp_path: Path) -> None:
    data_dir = tmp_path / "missing-data"
    database_path = tmp_path / "missing-state.db"
    settings = Settings(
        data_dir=data_dir,
        database_url=f"sqlite:///{database_path}",
    )

    report = standalone_diagnostic_service(settings).report()

    assert report.overall.value == "action_required"
    assert {check.check_id: check.status.value for check in report.checks} == {
        "database_connection": "fail",
        "database_schema": "fail",
        "data_directory": "fail",
        "disk_capacity": "fail",
        "artifact_references": "fail",
        "optional_model": "pass",
    }
    assert report.data_summary.documents == 0
    assert not data_dir.exists()
    assert not database_path.exists()
    serialized = report.model_dump_json()
    assert str(data_dir) not in serialized
    assert str(database_path) not in serialized


def test_diagnostic_detects_schema_drift_and_missing_artifacts(tmp_path: Path) -> None:
    data_dir = tmp_path / "workspace"
    storage = LocalStorage(data_dir)
    database = Database(f"sqlite:///{data_dir / 'legacy.db'}")
    database.create_all()

    schema_report = DiagnosticService(
        Settings(data_dir=data_dir, database_url=f"sqlite:///{data_dir / 'legacy.db'}"),
        database,
        storage,
    ).report()
    schema_check = next(
        check for check in schema_report.checks if check.check_id == "database_schema"
    )
    assert schema_report.overall.value == "action_required"
    assert schema_check.status.value == "fail"
    assert "000" not in schema_check.detail

    settings = Settings(
        data_dir=tmp_path / "current-workspace",
        database_url=f"sqlite:///{tmp_path / 'current-workspace' / 'state.db'}",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        source = Path("tests/fixtures/academic-comprehensive.docx")
        with source.open("rb") as stream:
            document = client.post(
                "/api/v1/documents",
                files={"file": ("missing.docx", stream)},
            ).json()
        analysis = client.post(
            f"/api/v1/documents/{document['document_id']}/analyze",
            json={"mode": "deterministic"},
        ).json()
        spec = client.post(
            "/api/v1/specs",
            json={
                "document_id": document["document_id"],
                "spec": default_academic_spec().model_dump(mode="json"),
            },
        ).json()
        with app.state.database.session_factory.begin() as session:
            session.add(
                JobRecord(
                    id="job_completed_without_artifacts",
                    document_id=document["document_id"],
                    analysis_id=analysis["analysis_id"],
                    spec_id=spec["spec_id"],
                    status="completed",
                    progress=100,
                )
            )
        artifact_report = client.get("/api/v1/diagnostics").json()

    artifact_check = next(
        check
        for check in artifact_report["checks"]
        if check["check_id"] == "artifact_references"
    )
    assert artifact_report["overall"] == "attention"
    assert artifact_check["status"] == "warning"
    assert "3 个" in artifact_check["detail"]
    assert "missing.docx" not in json.dumps(artifact_report, ensure_ascii=False)


def test_diagnostic_treats_outside_artifact_reference_as_invalid(tmp_path: Path) -> None:
    data_dir = tmp_path / "workspace"
    settings = Settings(
        data_dir=data_dir,
        database_url=f"sqlite:///{data_dir / 'state.db'}",
    )
    outside = tmp_path / "outside-private.docx"
    outside.write_bytes(b"private")
    app = create_app(settings)
    with TestClient(app) as client:
        source = Path("tests/fixtures/academic-comprehensive.docx")
        with source.open("rb") as stream:
            document = client.post(
                "/api/v1/documents",
                files={"file": ("private-name.docx", stream)},
            ).json()
        with app.state.database.session_factory.begin() as session:
            record = session.get(DocumentRecord, document["document_id"])
            assert record is not None
            record.stored_path = str(outside)
        payload = client.get("/api/v1/diagnostics").json()

    artifact_check = next(
        check
        for check in payload["checks"]
        if check["check_id"] == "artifact_references"
    )
    assert artifact_check["status"] == "warning"
    serialized = json.dumps(payload, ensure_ascii=False)
    assert str(outside) not in serialized
    assert "private-name.docx" not in serialized
    assert outside.read_bytes() == b"private"
