from __future__ import annotations

import logging
import uuid
from collections import Counter
from pathlib import Path

from docalign_core.analysis.classifier import (
    analyze_document,
    apply_role_overrides,
    build_analysis_summary,
)
from docalign_core.analysis.semantic import (
    SemanticAnalyzer,
    SemanticAnalyzerError,
    merge_semantic_analysis,
)
from docalign_core.config import Settings
from docalign_core.docx.manifest import extract_format_manifest
from docalign_core.docx.parser import parse_docx
from docalign_core.docx.safety import (
    DocxSafetyError,
    SafetyLimits,
    sha256_file,
    validate_docx_package,
)
from docalign_core.docx.text_import import PlainTextImportError, create_docx_from_text
from docalign_core.domain.audit import CONTENT_INTEGRITY_CODES, AuditReport
from docalign_core.domain.compliance import ComplianceReport, build_compliance_report
from docalign_core.domain.document_ir import (
    AnalysisResult,
    AnalysisSummary,
    ParagraphIR,
    RoleOverride,
)
from docalign_core.domain.enums import AnalysisMode, JobStatus
from docalign_core.domain.formatting_spec import (
    FormattingSpec,
    default_academic_spec,
    merge_specs,
)
from docalign_core.domain.manifest import FormatManifest
from docalign_core.llm.base import (
    DocumentSummary,
    RequirementCompilationError,
    RequirementCompilationResult,
    RequirementInterpreter,
)
from docalign_core.llm.openai_compatible import OpenAICompatibleChatInterpreter
from docalign_core.llm.semantic import OpenAICompatibleSemanticAnalyzer
from docalign_core.services.processing import ProcessingFailure, process_document
from docalign_core.validation.validator import DocumentValidator
from fastapi import UploadFile
from pydantic import ValidationError
from sqlalchemy import delete, select

from apps.api.db import (
    AnalysisRecord,
    Database,
    DocumentRecord,
    JobRecord,
    RoleOverrideRecord,
    SpecRecord,
    utcnow,
)
from apps.api.errors import ApiError
from apps.api.schemas import JobResponse, JobResultSummary
from apps.api.storage import LocalStorage

logger = logging.getLogger(__name__)


class ApiService:
    def __init__(self, settings: Settings, database: Database, storage: LocalStorage) -> None:
        self.settings = settings
        self.database = database
        self.storage = storage
        self.safety_limits = SafetyLimits(
            max_file_bytes=settings.max_upload_mb * 1024 * 1024,
            max_uncompressed_bytes=settings.max_uncompressed_mb * 1024 * 1024,
            max_entries=settings.max_zip_entries,
            max_compression_ratio=settings.max_compression_ratio,
        )
        self.requirement_interpreter: RequirementInterpreter | None = None
        self.semantic_analyzer: SemanticAnalyzer | None = None
        if settings.llm_configured:
            self.requirement_interpreter = OpenAICompatibleChatInterpreter(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                model=settings.llm_model,
                timeout_seconds=settings.llm_timeout_seconds,
                json_schema_mode=settings.llm_json_schema_mode,
            )
            self.semantic_analyzer = OpenAICompatibleSemanticAnalyzer(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                model=settings.llm_model,
                timeout_seconds=settings.llm_timeout_seconds,
                json_schema_mode=settings.llm_json_schema_mode,
            )

    async def create_document(self, upload: UploadFile) -> dict[str, object]:
        filename = _display_filename(upload.filename or "document.docx")
        if not filename.lower().endswith(".docx"):
            raise ApiError(415, "UNSUPPORTED_FILE_TYPE", "Only .docx files are supported.")
        document_id = f"doc_{uuid.uuid4().hex}"
        target = self.storage.upload_path(document_id)
        size = 0
        try:
            with target.open("wb") as output:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > self.safety_limits.max_file_bytes:
                        raise ApiError(
                            413,
                            "FILE_TOO_LARGE",
                            "The uploaded DOCX exceeds the configured limit.",
                        )
                    output.write(chunk)
            validate_docx_package(target, self.safety_limits)
        except (ApiError, DocxSafetyError):
            self.storage._remove(target.parent)
            raise
        record = DocumentRecord(
            id=document_id,
            original_filename=filename,
            stored_path=str(target),
            sha256=sha256_file(target),
            size_bytes=size,
        )
        with self.database.session_factory.begin() as session:
            session.add(record)
        return self.document_payload(record)

    def create_text_document(self, text: str, filename: str) -> dict[str, object]:
        display_name = _display_filename(filename)
        if not display_name.lower().endswith(".docx"):
            display_name = f"{display_name}.docx"
        document_id = f"doc_{uuid.uuid4().hex}"
        target = self.storage.upload_path(document_id)
        try:
            create_docx_from_text(text, target)
            validate_docx_package(target, self.safety_limits)
        except PlainTextImportError as exc:
            self.storage._remove(target.parent)
            raise ApiError(422, exc.code, exc.message) from exc
        except DocxSafetyError:
            self.storage._remove(target.parent)
            raise
        record = DocumentRecord(
            id=document_id,
            original_filename=display_name,
            stored_path=str(target),
            sha256=sha256_file(target),
            size_bytes=target.stat().st_size,
        )
        with self.database.session_factory.begin() as session:
            session.add(record)
        return self.document_payload(record)

    def get_document(self, document_id: str) -> DocumentRecord:
        with self.database.session_factory() as session:
            record = session.get(DocumentRecord, document_id)
            if record is None:
                raise ApiError(404, "DOCUMENT_NOT_FOUND", "Document not found.")
            session.expunge(record)
            return record

    def document_payload(self, record: DocumentRecord) -> dict[str, object]:
        return {
            "document_id": record.id,
            "filename": record.original_filename,
            "sha256": record.sha256,
            "size_bytes": record.size_bytes,
            "status": "uploaded",
            "created_at": record.created_at.isoformat(),
        }

    def delete_document(self, document_id: str) -> None:
        with self.database.session_factory.begin() as session:
            document = session.get(DocumentRecord, document_id)
            if document is None:
                raise ApiError(404, "DOCUMENT_NOT_FOUND", "Document not found.")
            analysis_ids = list(
                session.scalars(
                    select(AnalysisRecord.id).where(AnalysisRecord.document_id == document_id)
                )
            )
            job_ids = list(
                session.scalars(select(JobRecord.id).where(JobRecord.document_id == document_id))
            )
            session.delete(document)
        self.storage.delete_document_artifacts(document_id, analysis_ids, job_ids)

    async def analyze(
        self, document_id: str, mode: AnalysisMode = AnalysisMode.DETERMINISTIC
    ) -> tuple[str, AnalysisResult]:
        document = self.get_document(document_id)
        analysis_id = f"analysis_{uuid.uuid4().hex}"
        document_ir = parse_docx(
            Path(document.stored_path),
            document_id=document_id,
            safety_limits=self.safety_limits,
        )
        result = analyze_document(document_ir)
        if mode == AnalysisMode.SMART:
            if self.semantic_analyzer is None:
                raise ApiError(
                    503,
                    "SEMANTIC_ANALYSIS_NOT_CONFIGURED",
                    "Smart semantic analysis requires a compatible model endpoint.",
                )
            try:
                draft = await self.semantic_analyzer.analyze(document_ir, result)
            except SemanticAnalyzerError as exc:
                raise ApiError(502, exc.code, exc.message) from exc
            result = merge_semantic_analysis(
                result,
                draft,
                provider=self.semantic_analyzer.provider,
                model=self.semantic_analyzer.model,
            )
        result_path = self.storage.analysis_path(analysis_id)
        result_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        record = AnalysisRecord(
            id=analysis_id,
            document_id=document_id,
            source_sha256=document.sha256,
            result_path=str(result_path),
        )
        with self.database.session_factory.begin() as session:
            session.add(record)
        return analysis_id, result

    def get_analysis_record(self, analysis_id: str) -> AnalysisRecord:
        with self.database.session_factory() as session:
            record = session.get(AnalysisRecord, analysis_id)
            if record is None:
                raise ApiError(404, "ANALYSIS_NOT_FOUND", "Analysis not found.")
            session.expunge(record)
            return record

    def get_analysis(self, analysis_id: str) -> AnalysisResult:
        record = self.get_analysis_record(analysis_id)
        result = AnalysisResult.model_validate_json(
            Path(record.result_path).read_text(encoding="utf-8")
        )
        with self.database.session_factory() as session:
            rows = list(
                session.scalars(
                    select(RoleOverrideRecord).where(RoleOverrideRecord.analysis_id == analysis_id)
                )
            )
        overrides = [RoleOverride(node_id=row.node_id, role=row.role) for row in rows]
        if overrides:
            result.document_ir = apply_role_overrides(result.document_ir, overrides)
            result.summary = _summary(result)
        return result

    def set_role_overrides(self, analysis_id: str, overrides: list[RoleOverride]) -> AnalysisResult:
        result = self.get_analysis(analysis_id)
        valid_nodes = {
            block.node_id for block in result.document_ir.blocks if isinstance(block, ParagraphIR)
        }
        invalid = sorted({override.node_id for override in overrides} - valid_nodes)
        if invalid:
            raise ApiError(
                422,
                "INVALID_ROLE_OVERRIDE",
                "One or more role overrides reference unknown nodes.",
                {"node_ids": invalid},
            )
        with self.database.session_factory.begin() as session:
            session.execute(
                delete(RoleOverrideRecord).where(RoleOverrideRecord.analysis_id == analysis_id)
            )
            session.add_all(
                RoleOverrideRecord(
                    analysis_id=analysis_id,
                    node_id=override.node_id,
                    role=override.role.value,
                )
                for override in overrides
            )
        return self.get_analysis(analysis_id)

    def create_spec(
        self, spec: FormattingSpec, document_id: str | None = None
    ) -> tuple[str, FormattingSpec]:
        if document_id is not None:
            self.get_document(document_id)
        spec_id = f"spec_{uuid.uuid4().hex}"
        record = SpecRecord(
            id=spec_id,
            document_id=document_id,
            schema_version=spec.schema_version,
            json_payload=spec.model_dump_json(),
            source_type=spec.source.type.value,
        )
        with self.database.session_factory.begin() as session:
            session.add(record)
        return spec_id, spec

    def get_spec_record(self, spec_id: str) -> SpecRecord:
        with self.database.session_factory() as session:
            record = session.get(SpecRecord, spec_id)
            if record is None:
                raise ApiError(404, "SPEC_NOT_FOUND", "FormattingSpec not found.")
            session.expunge(record)
            return record

    def get_spec(self, spec_id: str) -> FormattingSpec:
        return FormattingSpec.model_validate_json(self.get_spec_record(spec_id).json_payload)

    def update_spec(self, spec_id: str, spec: FormattingSpec) -> FormattingSpec:
        with self.database.session_factory.begin() as session:
            record = session.get(SpecRecord, spec_id)
            if record is None:
                raise ApiError(404, "SPEC_NOT_FOUND", "FormattingSpec not found.")
            record.schema_version = spec.schema_version
            record.json_payload = spec.model_dump_json()
            record.source_type = spec.source.type.value
            record.updated_at = utcnow()
        return spec

    async def compile_spec(
        self,
        instruction: str,
        *,
        document_id: str | None = None,
        analysis_id: str | None = None,
        apply_preset: bool = False,
    ) -> tuple[str, RequirementCompilationResult]:
        if self.requirement_interpreter is None:
            raise ApiError(
                503,
                "LLM_NOT_CONFIGURED",
                "Natural-language compilation is disabled until a compatible endpoint "
                "is configured.",
            )
        summary: DocumentSummary | None = None
        if analysis_id:
            analysis_record = self.get_analysis_record(analysis_id)
            if document_id and analysis_record.document_id != document_id:
                raise ApiError(
                    409,
                    "ANALYSIS_SOURCE_MISMATCH",
                    "The analysis does not belong to the requested document.",
                )
            analysis = self.get_analysis(analysis_id)
            summary = DocumentSummary(
                paragraph_count=analysis.summary.paragraph_count,
                table_count=analysis.summary.table_count,
                image_count=analysis.summary.image_count,
                existing_styles=analysis.summary.existing_styles,
                detected_roles=analysis.summary.role_counts,
                analysis_mode=analysis.summary.analysis_mode.value,
                document_kind=(
                    analysis.summary.document_kind.value if analysis.summary.document_kind else None
                ),
            )
            document_id = analysis_record.document_id
        try:
            compilation = await self.requirement_interpreter.compile_requirements(
                instruction, summary
            )
        except RequirementCompilationError as exc:
            raise ApiError(502, exc.code, exc.message) from exc
        if apply_preset:
            try:
                merged = merge_specs(default_academic_spec(), compilation.spec)
            except ValidationError as exc:
                logger.warning("Incompatible formatting rules", exc_info=exc)
                raise ApiError(
                    422,
                    "FORMATTING_SPEC_CONFLICT",
                    "The preset and requested rules contain incompatible paragraph settings.",
                ) from exc
            compilation = compilation.model_copy(
                update={
                    "spec": merged,
                    "assumptions": merged.source.assumptions,
                }
            )
        spec_id, _ = self.create_spec(compilation.spec, document_id)
        return spec_id, compilation

    def create_job(self, document_id: str, analysis_id: str, spec_id: str) -> JobRecord:
        document = self.get_document(document_id)
        analysis = self.get_analysis_record(analysis_id)
        spec = self.get_spec_record(spec_id)
        current_sha256 = sha256_file(Path(document.stored_path))
        if (
            analysis.document_id != document_id
            or analysis.source_sha256 != document.sha256
            or current_sha256 != document.sha256
        ):
            raise ApiError(
                409,
                "ANALYSIS_SOURCE_MISMATCH",
                "The analysis does not match the current source document.",
            )
        if spec.document_id not in {None, document_id}:
            raise ApiError(409, "SPEC_DOCUMENT_MISMATCH", "The spec belongs to another document.")
        record = JobRecord(
            id=f"job_{uuid.uuid4().hex}",
            document_id=document_id,
            analysis_id=analysis_id,
            spec_id=spec_id,
            status=JobStatus.QUEUED.value,
            progress=0,
        )
        with self.database.session_factory.begin() as session:
            session.add(record)
        return record

    def audit_compliance(
        self,
        document_id: str,
        analysis_id: str,
        spec_id: str,
    ) -> ComplianceReport:
        document = self.get_document(document_id)
        analysis_record = self.get_analysis_record(analysis_id)
        spec_record = self.get_spec_record(spec_id)
        current_sha256 = sha256_file(Path(document.stored_path))
        if (
            analysis_record.document_id != document_id
            or analysis_record.source_sha256 != document.sha256
            or current_sha256 != document.sha256
        ):
            raise ApiError(
                409,
                "ANALYSIS_SOURCE_MISMATCH",
                "The compliance analysis does not match the current source document.",
            )
        if spec_record.document_id not in {None, document_id}:
            raise ApiError(
                409,
                "SPEC_DOCUMENT_MISMATCH",
                "The compliance spec belongs to another document.",
            )
        analysis = self.get_analysis(analysis_id)
        spec = self.get_spec(spec_id)
        validation = DocumentValidator().validate(
            Path(document.stored_path),
            spec,
            analysis.document_ir,
        )
        return build_compliance_report(
            validation,
            document_id=document_id,
            analysis_id=analysis_id,
            spec_id=spec_id,
        )

    def extract_manifest(self, document_id: str) -> FormatManifest:
        document = self.get_document(document_id)
        current_sha256 = sha256_file(Path(document.stored_path))
        if current_sha256 != document.sha256:
            raise ApiError(
                409,
                "DOCUMENT_SOURCE_MISMATCH",
                "The stored document changed after upload.",
            )
        return extract_format_manifest(
            Path(document.stored_path),
            document_id=document_id,
            source_filename=document.original_filename,
        )

    def get_job(self, job_id: str) -> JobRecord:
        with self.database.session_factory() as session:
            record = session.get(JobRecord, job_id)
            if record is None:
                raise ApiError(404, "JOB_NOT_FOUND", "Processing job not found.")
            session.expunge(record)
            return record

    def job_payload(self, record: JobRecord) -> JobResponse:
        completed = record.status == JobStatus.COMPLETED.value
        auto_layout_splits = 0
        result_summary: JobResultSummary | None = None
        if record.audit_json_path and Path(record.audit_json_path).exists():
            try:
                audit = AuditReport.model_validate_json(
                    Path(record.audit_json_path).read_text(encoding="utf-8")
                )
                auto_layout_splits = audit.summary.auto_layout_splits
                result_summary = _job_result_summary(audit)
            except (OSError, UnicodeError, ValidationError):
                auto_layout_splits = 0
        return JobResponse(
            job_id=record.id,
            document_id=record.document_id,
            analysis_id=record.analysis_id,
            spec_id=record.spec_id,
            status=JobStatus(record.status),
            progress=record.progress,
            auto_layout_splits=auto_layout_splits,
            result_summary=result_summary,
            output_document_url=f"/api/v1/jobs/{record.id}/output" if completed else None,
            audit_json_url=(
                f"/api/v1/jobs/{record.id}/audit.json" if record.audit_json_path else None
            ),
            audit_markdown_url=(
                f"/api/v1/jobs/{record.id}/audit.md" if record.audit_markdown_path else None
            ),
            error_code=record.error_code,
            error_message=record.error_message,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def run_job(self, job_id: str) -> None:
        try:
            self._set_job_state(job_id, JobStatus.ANALYZING, 15)
            job = self.get_job(job_id)
            document = self.get_document(job.document_id)
            analysis_record = self.get_analysis_record(job.analysis_id)
            if (
                analysis_record.source_sha256 != document.sha256
                or sha256_file(Path(document.stored_path)) != document.sha256
            ):
                raise ApiError(
                    409,
                    "ANALYSIS_SOURCE_MISMATCH",
                    "The source changed after analysis.",
                )
            analysis = self.get_analysis(job.analysis_id)
            spec = self.get_spec(job.spec_id)
            self._set_job_state(job_id, JobStatus.PLANNING, 30)
            self._set_job_state(job_id, JobStatus.FORMATTING, 45)
            output = self.storage.output_path(job_id)
            artifacts = self.storage.job_dir(job_id)
            result = process_document(
                Path(document.stored_path),
                analysis.document_ir,
                spec,
                output,
                job_id=job_id,
                artifact_dir=artifacts,
            )
            self._set_job_state(
                job_id,
                JobStatus.COMPLETED,
                100,
                output_path=result.output_path,
                audit_json_path=result.audit_json_path,
                audit_markdown_path=result.audit_markdown_path,
            )
        except ProcessingFailure as exc:
            self._fail_job(job_id, exc.code, exc.message)
        except ApiError as exc:
            self._fail_job(job_id, exc.code, exc.message)
        except ValidationError:
            logger.exception("Formatting specification conflict", extra={"job_id": job_id})
            self._fail_job(
                job_id,
                "FORMATTING_SPEC_CONFLICT",
                "The merged formatting rules contain incompatible paragraph settings.",
            )
        except Exception as exc:  # final background-task fault boundary
            logger.exception("Unmapped job failure", extra={"job_id": job_id})
            self._fail_job(
                job_id,
                "FORMAT_APPLICATION_FAILED",
                f"Unexpected processing failure: {type(exc).__name__}.",
            )

    def _set_job_state(
        self,
        job_id: str,
        status: JobStatus,
        progress: int,
        **paths: str,
    ) -> None:
        with self.database.session_factory.begin() as session:
            record = session.get(JobRecord, job_id)
            if record is None:
                raise ApiError(404, "JOB_NOT_FOUND", "Processing job not found.")
            record.status = status.value
            record.progress = progress
            record.updated_at = utcnow()
            for name, value in paths.items():
                setattr(record, name, value)

    def _fail_job(self, job_id: str, code: str, message: str) -> None:
        with self.database.session_factory.begin() as session:
            record = session.get(JobRecord, job_id)
            if record is None:
                return
            audit_json = self.storage.job_dir(job_id) / "audit.json"
            audit_markdown = self.storage.job_dir(job_id) / "audit.md"
            record.status = JobStatus.FAILED.value
            record.error_code = code
            record.error_message = message
            record.audit_json_path = str(audit_json) if audit_json.exists() else None
            record.audit_markdown_path = str(audit_markdown) if audit_markdown.exists() else None
            record.updated_at = utcnow()


def _summary(result: AnalysisResult) -> AnalysisSummary:
    previous = result.summary
    return build_analysis_summary(
        result.document_ir,
        analysis_mode=previous.analysis_mode,
        document_kind=previous.document_kind,
        document_kind_confidence=previous.document_kind_confidence,
        model_reviewed_paragraphs=previous.model_reviewed_paragraphs,
        model_provider=previous.model_provider,
        model_name=previous.model_name,
    )


def _job_result_summary(audit: AuditReport) -> JobResultSummary:
    categories = Counter(
        _change_category(mutation.property_path)
        for mutation in audit.mutations
        if mutation.status == "changed"
    )
    return JobResultSummary(
        validation_passed=audit.validation.valid,
        content_integrity_passed=not any(
            issue.code in CONTENT_INTEGRITY_CODES for issue in audit.validation.issues
        ),
        format_operations=audit.summary.format_operations,
        changed_mutations=audit.summary.changed_mutations,
        change_categories=dict(sorted(categories.items())),
        warning_count=len(audit.warnings),
        validation_issue_count=len(audit.validation.issues),
        remaining_review_items=audit.summary.unknown_blocks,
        paragraphs_before=audit.summary.paragraphs_before,
        paragraphs_after=audit.summary.paragraphs_after,
        auto_layout_splits=audit.summary.auto_layout_splits,
    )


def _change_category(property_path: str) -> str:
    if property_path == "paragraph.structure":
        return "structure"
    if property_path.startswith("section."):
        return "page_layout"
    if property_path.startswith(("styles.", "paragraph.")):
        return "paragraph_styles"
    if property_path.startswith("runs."):
        return "text_font"
    if property_path.startswith(("table.", "cell.")):
        return "tables"
    if property_path.startswith(("header.", "footer.")):
        return "header_footer"
    if property_path.startswith("visual_cleanup."):
        return "visual_cleanup"
    return "other"


def _display_filename(filename: str) -> str:
    cleaned = filename.replace("\\", "/").split("/")[-1].strip()
    return cleaned[:512] or "document.docx"
