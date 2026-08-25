from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from apps.api.db import AnalysisRecord, Database, DocumentRecord, JobRecord, SpecRecord


def test_restart_marks_active_jobs_interrupted_and_document_delete_cascades(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite:///{tmp_path / 'state.db'}")
    database.create_all()
    with database.session_factory.begin() as session:
        session.add(
            DocumentRecord(
                id="doc_1",
                original_filename="source.docx",
                stored_path="/tmp/source.docx",
                sha256="a" * 64,
                size_bytes=1,
            )
        )
        session.flush()
        session.add(
            AnalysisRecord(
                id="analysis_1",
                document_id="doc_1",
                source_sha256="a" * 64,
                result_path="/tmp/analysis.json",
            )
        )
        session.add(
            SpecRecord(
                id="spec_1",
                document_id="doc_1",
                schema_version="formatting-spec.v1",
                json_payload="{}",
                source_type="structured",
            )
        )
        session.flush()
        session.add(
            JobRecord(
                id="job_1",
                document_id="doc_1",
                analysis_id="analysis_1",
                spec_id="spec_1",
                status="formatting",
                progress=45,
            )
        )

    database.mark_interrupted_jobs()
    with database.session_factory() as session:
        job = session.get(JobRecord, "job_1")
        assert job is not None
        assert job.status == "failed"
        assert job.error_code == "JOB_INTERRUPTED"
        document = session.get(DocumentRecord, "doc_1")
        assert document is not None
        session.delete(document)
        session.commit()
        assert session.scalar(select(func.count()).select_from(JobRecord)) == 0
        assert session.scalar(select(func.count()).select_from(AnalysisRecord)) == 0
        assert session.scalar(select(func.count()).select_from(SpecRecord)) == 0
