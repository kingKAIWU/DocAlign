from __future__ import annotations

import json
import logging
import tempfile
import unicodedata
import uuid
from datetime import datetime
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
from docalign_core.docx.template_candidate import compile_template_rule_candidate
from docalign_core.docx.text_import import PlainTextImportError, create_docx_from_text
from docalign_core.domain.audit import (
    AuditReport,
    ProcessingBoundaryAcknowledgmentMethod,
)
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
from docalign_core.domain.rule_pack import (
    RulePackApprovalStatus,
    RulePackArtifact,
    RulePackImportSource,
    canonical_formatting_spec_json,
    formatting_spec_sha256,
    rule_pack_artifact_sha256,
)
from docalign_core.domain.template_candidate import TemplateRuleCandidate
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
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from apps.api.capacity import (
    WorkspaceCapacityGuard,
    is_capacity_error,
    processing_working_bytes,
    upload_working_bytes,
)
from apps.api.change_summary import build_job_result_summary
from apps.api.db import (
    AnalysisRecord,
    Database,
    DocumentRecord,
    JobRecord,
    RoleOverrideRecord,
    RulePackRecord,
    RulePackVersionRecord,
    SpecRecord,
    as_utc,
    utcnow,
)
from apps.api.errors import ApiError
from apps.api.schemas import (
    JobResponse,
    JobResultSummary,
    RulePackCatalogItem,
    RulePackCatalogResponse,
    RulePackDetailResponse,
    RulePackImportPreview,
    RulePackImportResult,
    RulePackVersionSummary,
)
from apps.api.storage import LocalStorage

logger = logging.getLogger(__name__)


class ApiService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        storage: LocalStorage,
        capacity: WorkspaceCapacityGuard,
    ) -> None:
        self.settings = settings
        self.database = database
        self.storage = storage
        self.capacity = capacity
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
        size_hint = upload.size
        if size_hint is not None and size_hint > self.safety_limits.max_file_bytes:
            raise ApiError(
                413,
                "FILE_TOO_LARGE",
                "The uploaded DOCX exceeds the configured limit.",
            )
        self.capacity.ensure(
            upload_working_bytes(size_hint, self.safety_limits.max_file_bytes),
            operation="document_upload",
        )
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
        except OSError as exc:
            self.storage._remove(target.parent)
            if is_capacity_error(exc):
                raise self.capacity.api_error(operation="document_upload") from exc
            raise
        record = DocumentRecord(
            id=document_id,
            original_filename=filename,
            stored_path=str(target),
            sha256=sha256_file(target),
            size_bytes=size,
        )
        try:
            with self.database.session_factory.begin() as session:
                session.add(record)
        except SQLAlchemyError as exc:
            self.storage._remove(target.parent)
            if is_capacity_error(exc):
                raise self.capacity.api_error(operation="document_upload") from exc
            raise
        return self.document_payload(record)

    async def compile_template_candidate(self, upload: UploadFile) -> TemplateRuleCandidate:
        filename = _display_filename(upload.filename or "reference.docx")
        if not filename.lower().endswith(".docx"):
            raise ApiError(415, "UNSUPPORTED_FILE_TYPE", "Only .docx files are supported.")
        with tempfile.TemporaryDirectory(prefix="docalign-template-") as directory:
            target = Path(directory) / "reference.docx"
            size = 0
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
            return compile_template_rule_candidate(target, source_filename=filename)

    def create_text_document(self, text: str, filename: str) -> dict[str, object]:
        display_name = _display_filename(filename)
        if not display_name.lower().endswith(".docx"):
            display_name = f"{display_name}.docx"
        self.capacity.ensure(
            upload_working_bytes(len(text.encode("utf-8")), self.safety_limits.max_file_bytes),
            operation="text_document_import",
        )
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
        except OSError as exc:
            self.storage._remove(target.parent)
            if is_capacity_error(exc):
                raise self.capacity.api_error(operation="text_document_import") from exc
            raise
        record = DocumentRecord(
            id=document_id,
            original_filename=display_name,
            stored_path=str(target),
            sha256=sha256_file(target),
            size_bytes=target.stat().st_size,
        )
        try:
            with self.database.session_factory.begin() as session:
                session.add(record)
        except SQLAlchemyError as exc:
            self.storage._remove(target.parent)
            if is_capacity_error(exc):
                raise self.capacity.api_error(operation="text_document_import") from exc
            raise
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
        active_statuses = {
            JobStatus.QUEUED.value,
            JobStatus.ANALYZING.value,
            JobStatus.PLANNING.value,
            JobStatus.FORMATTING.value,
            JobStatus.VALIDATING.value,
            JobStatus.REPAIRING.value,
            JobStatus.CANCELING.value,
        }
        with self.database.session_factory.begin() as session:
            document = session.get(DocumentRecord, document_id)
            if document is None:
                raise ApiError(404, "DOCUMENT_NOT_FOUND", "Document not found.")
            active_job_id = session.scalar(
                select(JobRecord.id).where(
                    JobRecord.document_id == document_id,
                    JobRecord.status.in_(active_statuses),
                )
            )
            if active_job_id is not None:
                raise ApiError(
                    409,
                    "DOCUMENT_JOB_ACTIVE",
                    "Wait for the active processing job to finish before deleting this document.",
                )
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
        self.capacity.ensure(
            processing_working_bytes(document.size_bytes),
            operation="document_analysis",
        )
        analysis_id = f"analysis_{uuid.uuid4().hex}"
        document_ir = parse_docx(
            Path(document.stored_path),
            document_id=document_id,
            source_filename=document.original_filename,
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
        try:
            result_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        except OSError as exc:
            self.storage._remove(result_path.parent)
            if is_capacity_error(exc):
                raise self.capacity.api_error(operation="document_analysis") from exc
            raise
        record = AnalysisRecord(
            id=analysis_id,
            document_id=document_id,
            source_sha256=document.sha256,
            result_path=str(result_path),
        )
        try:
            with self.database.session_factory.begin() as session:
                session.add(record)
        except SQLAlchemyError as exc:
            self.storage._remove(result_path.parent)
            if is_capacity_error(exc):
                raise self.capacity.api_error(operation="document_analysis") from exc
            raise
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

    def list_rule_packs(self) -> RulePackCatalogResponse:
        with self.database.session_factory() as session:
            packs = list(
                session.scalars(
                    select(RulePackRecord).order_by(
                        RulePackRecord.updated_at.desc(), RulePackRecord.name.asc()
                    )
                )
            )
            items: list[RulePackCatalogItem] = []
            for pack in packs:
                version = session.get(RulePackVersionRecord, (pack.id, pack.current_revision))
                if version is None:
                    raise ApiError(
                        500,
                        "RULE_PACK_INTEGRITY_FAILED",
                        "The current rule-pack revision is missing.",
                        {"pack_id": pack.id, "revision": pack.current_revision},
                    )
                items.append(
                    RulePackCatalogItem(
                        pack_id=pack.id,
                        name=pack.name,
                        description=pack.description,
                        scope_label=pack.scope_label,
                        current_revision=pack.current_revision,
                        current_approval_status=RulePackApprovalStatus(version.approval_status),
                        current_spec_sha256=version.spec_sha256,
                        created_at=pack.created_at,
                        updated_at=pack.updated_at,
                    )
                )
        return RulePackCatalogResponse(rule_packs=items)

    async def preview_rule_pack_import(self, upload: UploadFile) -> RulePackImportPreview:
        artifact = await self._read_rule_pack_import(upload)
        artifact_sha256 = rule_pack_artifact_sha256(artifact)
        source = self._rule_pack_import_source(artifact, artifact_sha256)
        existing = self._find_existing_portable_artifact(artifact, artifact_sha256)
        suggested_name, name_conflict = self._suggest_rule_pack_import_name(artifact.name)
        if existing is not None:
            suggested_name = existing.name
        return RulePackImportPreview(
            source=source,
            suggested_name=suggested_name,
            source_name_conflict=name_conflict,
            already_present=existing is not None,
            existing_pack_id=existing.pack_id if existing else None,
            existing_revision=existing.revision if existing else None,
            warnings=[
                "rule-pack.v1 does not contain a verifiable publisher signature",
                "source approval is recorded as provenance but imported approval is reset to draft",
                "the artifact contains one immutable revision, not the source "
                "package's full history",
            ],
        )

    async def import_rule_pack(
        self,
        upload: UploadFile,
        *,
        request_id: str,
        name: str,
    ) -> RulePackImportResult:
        artifact = await self._read_rule_pack_import(upload)
        artifact_sha256 = rule_pack_artifact_sha256(artifact)
        source = self._rule_pack_import_source(artifact, artifact_sha256)
        normalized_name = " ".join(name.split())
        if not normalized_name:
            raise ApiError(
                422,
                "RULE_PACK_IMPORT_NAME_REQUIRED",
                "The imported rule pack requires a visible local name.",
            )
        if len(normalized_name) > 120:
            raise ApiError(
                422,
                "RULE_PACK_IMPORT_NAME_INVALID",
                "The imported rule-pack name cannot exceed 120 characters.",
            )

        retried = self._existing_rule_pack_import_request(
            request_id=request_id,
            artifact_sha256=artifact_sha256,
            name=normalized_name,
        )
        if retried is not None:
            return RulePackImportResult(artifact=retried, already_present=False)

        existing = self._find_existing_portable_artifact(artifact, artifact_sha256)
        if existing is not None:
            return RulePackImportResult(artifact=existing, already_present=True)

        pack_id = f"pack_{uuid.uuid4().hex}"
        created_at = utcnow()
        pack = RulePackRecord(
            id=pack_id,
            name=normalized_name,
            name_key=_rule_pack_name_key(normalized_name),
            description=artifact.description.strip(),
            scope_label=" ".join(artifact.scope_label.split()),
            current_revision=1,
            created_at=created_at,
            updated_at=created_at,
        )
        source_state = (
            "本地已确认"
            if artifact.approval_status == RulePackApprovalStatus.LOCALLY_APPROVED
            else "草稿"
        )
        version = self._new_rule_pack_version_record(
            pack_id=pack_id,
            revision=1,
            request_id=request_id,
            spec=artifact.spec,
            change_note=(
                f"从跨机规则包 {artifact.pack_id} 修订 {artifact.revision} 导入；"
                f"来源状态为{source_state}，需在本机重新核对"
            ),
            approval_status=RulePackApprovalStatus.DRAFT,
            approval_note=None,
            restored_from_revision=None,
            created_at=created_at,
            import_source=source,
        )
        try:
            with self.database.session_factory.begin() as session:
                session.add(pack)
                session.add(version)
        except IntegrityError as exc:
            retried = self._existing_rule_pack_import_request(
                request_id=request_id,
                artifact_sha256=artifact_sha256,
                name=normalized_name,
            )
            if retried is not None:
                return RulePackImportResult(artifact=retried, already_present=False)
            existing = self._find_existing_portable_artifact(artifact, artifact_sha256)
            if existing is not None:
                return RulePackImportResult(artifact=existing, already_present=True)
            raise ApiError(
                409,
                "RULE_PACK_NAME_CONFLICT",
                "A rule pack with the same name already exists.",
                {"name": normalized_name},
            ) from exc
        return RulePackImportResult(
            artifact=self.get_rule_pack_artifact(pack_id, 1),
            already_present=False,
        )

    async def _read_rule_pack_import(self, upload: UploadFile) -> RulePackArtifact:
        filename = _display_filename(upload.filename or "rule-pack.json")
        if Path(filename).suffix.casefold() != ".json":
            raise ApiError(
                415,
                "RULE_PACK_IMPORT_UNSUPPORTED_FILE",
                "Only portable .json rule-pack artifacts can be imported.",
            )
        max_bytes = self.settings.max_rule_pack_import_kb * 1024
        payload = await upload.read(max_bytes + 1)
        if len(payload) > max_bytes:
            raise ApiError(
                413,
                "RULE_PACK_IMPORT_TOO_LARGE",
                "The portable rule-pack artifact exceeds the configured limit.",
                {"max_kb": self.settings.max_rule_pack_import_kb},
            )
        if not payload:
            raise ApiError(
                422,
                "RULE_PACK_IMPORT_INVALID",
                "The portable rule-pack artifact is empty.",
            )
        try:
            return RulePackArtifact.model_validate_json(payload)
        except ValidationError as exc:
            safe_errors = [
                {
                    "location": ".".join(str(item) for item in error["loc"]),
                    "message": error["msg"],
                    "type": error["type"],
                }
                for error in exc.errors(include_input=False, include_url=False)[:20]
            ]
            integrity_failed = any(
                "sha256 does not match" in error["message"] for error in safe_errors
            )
            raise ApiError(
                422,
                (
                    "RULE_PACK_IMPORT_INTEGRITY_FAILED"
                    if integrity_failed
                    else "RULE_PACK_IMPORT_INVALID"
                ),
                (
                    "The portable rule-pack artifact failed its integrity check."
                    if integrity_failed
                    else "The portable rule-pack artifact does not match rule-pack.v1."
                ),
                {"errors": safe_errors},
            ) from exc

    @staticmethod
    def _rule_pack_import_source(
        artifact: RulePackArtifact, artifact_sha256: str
    ) -> RulePackImportSource:
        return RulePackImportSource(
            artifact_sha256=artifact_sha256,
            pack_id=artifact.pack_id,
            request_id=artifact.request_id,
            name=artifact.name,
            scope_label=artifact.scope_label,
            revision=artifact.revision,
            approval_status=artifact.approval_status,
            approval_note=artifact.approval_note,
            change_note=artifact.change_note,
            spec_sha256=artifact.spec_sha256,
            created_at=artifact.created_at,
        )

    def _find_existing_portable_artifact(
        self, artifact: RulePackArtifact, artifact_sha256: str
    ) -> RulePackArtifact | None:
        imported_pair: tuple[str, int] | None = None
        direct_pair: tuple[str, int] | None = None
        with self.database.session_factory() as session:
            imported = session.scalar(
                select(RulePackVersionRecord).where(
                    RulePackVersionRecord.import_source_artifact_sha256 == artifact_sha256
                )
            )
            if imported is not None:
                imported_pair = (imported.pack_id, imported.revision)
            direct = session.get(
                RulePackVersionRecord,
                (artifact.pack_id, artifact.revision),
            )
            if direct is not None:
                direct_pair = (direct.pack_id, direct.revision)
        if imported_pair is not None:
            return self.get_rule_pack_artifact(*imported_pair)
        if direct_pair is not None:
            local = self.get_rule_pack_artifact(*direct_pair)
            if rule_pack_artifact_sha256(local) == artifact_sha256:
                return local
        return None

    def _existing_rule_pack_import_request(
        self, *, request_id: str, artifact_sha256: str, name: str
    ) -> RulePackArtifact | None:
        with self.database.session_factory() as session:
            version = session.scalar(
                select(RulePackVersionRecord).where(RulePackVersionRecord.request_id == request_id)
            )
            if version is None:
                return None
            pack_id, revision = version.pack_id, version.revision
        existing = self.get_rule_pack_artifact(pack_id, revision)
        if (
            existing.name != name
            or existing.import_source is None
            or existing.import_source.artifact_sha256 != artifact_sha256
        ):
            raise ApiError(
                409,
                "IDEMPOTENCY_KEY_REUSED",
                "The request identifier was already used for different rule-pack content.",
                {"request_id": request_id},
            )
        return existing

    def _suggest_rule_pack_import_name(self, source_name: str) -> tuple[str, bool]:
        normalized = " ".join(source_name.split())
        with self.database.session_factory() as session:
            if not self._rule_pack_name_exists(session, normalized):
                return normalized, False
            for index in range(1, 10_000):
                suffix = "（导入）" if index == 1 else f"（导入 {index}）"
                candidate = f"{normalized[: 120 - len(suffix)]}{suffix}"
                if not self._rule_pack_name_exists(session, candidate):
                    return candidate, True
        raise ApiError(
            409,
            "RULE_PACK_NAME_CONFLICT",
            "No available local name could be suggested for the imported rule pack.",
        )

    @staticmethod
    def _rule_pack_name_exists(session: Session, name: str) -> bool:
        return bool(
            session.scalar(
                select(RulePackRecord.id).where(
                    RulePackRecord.name_key == _rule_pack_name_key(name)
                )
            )
        )

    def create_rule_pack(
        self,
        *,
        request_id: str,
        name: str,
        description: str,
        scope_label: str,
        spec: FormattingSpec,
        change_note: str,
        approval_status: RulePackApprovalStatus,
        approval_note: str | None,
    ) -> RulePackArtifact:
        normalized_name = " ".join(name.split())
        existing = self._existing_rule_pack_write(
            request_id=request_id,
            pack_id=None,
            spec=spec,
            change_note=change_note,
            approval_status=approval_status,
            approval_note=approval_note,
            restored_from_revision=None,
            name=normalized_name,
            description=description,
            scope_label=scope_label,
        )
        if existing is not None:
            return existing
        pack_id = f"pack_{uuid.uuid4().hex}"
        created_at = utcnow()
        pack = RulePackRecord(
            id=pack_id,
            name=normalized_name,
            name_key=_rule_pack_name_key(normalized_name),
            description=description,
            scope_label=scope_label,
            current_revision=1,
            created_at=created_at,
            updated_at=created_at,
        )
        version = self._new_rule_pack_version_record(
            pack_id=pack_id,
            revision=1,
            request_id=request_id,
            spec=spec,
            change_note=change_note,
            approval_status=approval_status,
            approval_note=approval_note,
            restored_from_revision=None,
            created_at=created_at,
        )
        try:
            with self.database.session_factory.begin() as session:
                session.add(pack)
                session.add(version)
        except IntegrityError as exc:
            retried = self._existing_rule_pack_write(
                request_id=request_id,
                pack_id=None,
                spec=spec,
                change_note=change_note,
                approval_status=approval_status,
                approval_note=approval_note,
                restored_from_revision=None,
                name=normalized_name,
                description=description,
                scope_label=scope_label,
            )
            if retried is not None:
                return retried
            raise ApiError(
                409,
                "RULE_PACK_NAME_CONFLICT",
                "A rule pack with the same name already exists.",
                {"name": normalized_name},
            ) from exc
        return self.get_rule_pack_artifact(pack_id, 1)

    def get_rule_pack_detail(self, pack_id: str) -> RulePackDetailResponse:
        with self.database.session_factory() as session:
            pack = session.get(RulePackRecord, pack_id)
            if pack is None:
                raise ApiError(404, "RULE_PACK_NOT_FOUND", "Rule pack not found.")
            versions = list(
                session.scalars(
                    select(RulePackVersionRecord)
                    .where(RulePackVersionRecord.pack_id == pack_id)
                    .order_by(RulePackVersionRecord.revision.desc())
                )
            )
            return RulePackDetailResponse(
                pack_id=pack.id,
                name=pack.name,
                description=pack.description,
                scope_label=pack.scope_label,
                current_revision=pack.current_revision,
                created_at=pack.created_at,
                updated_at=pack.updated_at,
                versions=[self._rule_pack_version_summary(item) for item in versions],
            )

    def get_rule_pack_artifact(self, pack_id: str, revision: int) -> RulePackArtifact:
        with self.database.session_factory() as session:
            pack = session.get(RulePackRecord, pack_id)
            if pack is None:
                raise ApiError(404, "RULE_PACK_NOT_FOUND", "Rule pack not found.")
            version = session.get(RulePackVersionRecord, (pack_id, revision))
            if version is None:
                raise ApiError(
                    404,
                    "RULE_PACK_VERSION_NOT_FOUND",
                    "Rule-pack revision not found.",
                    {"pack_id": pack_id, "revision": revision},
                )
            try:
                spec = FormattingSpec.model_validate_json(version.json_payload)
            except ValidationError as exc:
                raise ApiError(
                    500,
                    "RULE_PACK_INTEGRITY_FAILED",
                    "The stored rule-pack revision is invalid.",
                    {"pack_id": pack_id, "revision": revision},
                ) from exc
            digest = formatting_spec_sha256(spec)
            if digest != version.spec_sha256:
                raise ApiError(
                    500,
                    "RULE_PACK_INTEGRITY_FAILED",
                    "The stored rule-pack revision failed its integrity check.",
                    {"pack_id": pack_id, "revision": revision},
                )
            import_source = self._stored_rule_pack_import_source(version)
            return RulePackArtifact(
                pack_id=pack.id,
                request_id=version.request_id,
                name=pack.name,
                description=pack.description,
                scope_label=pack.scope_label,
                revision=version.revision,
                approval_status=RulePackApprovalStatus(version.approval_status),
                approval_note=version.approval_note,
                change_note=version.change_note,
                restored_from_revision=version.restored_from_revision,
                spec_sha256=version.spec_sha256,
                created_at=version.created_at,
                spec=spec,
                import_source=import_source,
            )

    def create_rule_pack_version(
        self,
        pack_id: str,
        *,
        request_id: str,
        spec: FormattingSpec,
        change_note: str,
        approval_status: RulePackApprovalStatus,
        approval_note: str | None,
        restored_from_revision: int | None = None,
    ) -> RulePackArtifact:
        existing = self._existing_rule_pack_write(
            request_id=request_id,
            pack_id=pack_id,
            spec=spec,
            change_note=change_note,
            approval_status=approval_status,
            approval_note=approval_note,
            restored_from_revision=restored_from_revision,
        )
        if existing is not None:
            return existing
        created_at = utcnow()
        revision = 0
        try:
            with self.database.session_factory.begin() as session:
                pack = session.get(RulePackRecord, pack_id)
                if pack is None:
                    raise ApiError(404, "RULE_PACK_NOT_FOUND", "Rule pack not found.")
                revision = pack.current_revision + 1
                session.add(
                    self._new_rule_pack_version_record(
                        pack_id=pack_id,
                        revision=revision,
                        request_id=request_id,
                        spec=spec,
                        change_note=change_note,
                        approval_status=approval_status,
                        approval_note=approval_note,
                        restored_from_revision=restored_from_revision,
                        created_at=created_at,
                    )
                )
                pack.current_revision = revision
                pack.updated_at = created_at
        except IntegrityError as exc:
            retried = self._existing_rule_pack_write(
                request_id=request_id,
                pack_id=pack_id,
                spec=spec,
                change_note=change_note,
                approval_status=approval_status,
                approval_note=approval_note,
                restored_from_revision=restored_from_revision,
            )
            if retried is not None:
                return retried
            raise ApiError(
                409,
                "RULE_PACK_VERSION_CONFLICT",
                "The rule pack changed while this revision was being saved. Reload and retry.",
                {"pack_id": pack_id},
            ) from exc
        return self.get_rule_pack_artifact(pack_id, revision)

    def restore_rule_pack_revision(
        self, pack_id: str, revision: int, change_note: str, request_id: str
    ) -> RulePackArtifact:
        restored = self.get_rule_pack_artifact(pack_id, revision)
        return self.create_rule_pack_version(
            pack_id,
            request_id=request_id,
            spec=restored.spec,
            change_note=change_note,
            approval_status=RulePackApprovalStatus.DRAFT,
            approval_note=None,
            restored_from_revision=revision,
        )

    @staticmethod
    def _new_rule_pack_version_record(
        *,
        pack_id: str,
        revision: int,
        request_id: str,
        spec: FormattingSpec,
        change_note: str,
        approval_status: RulePackApprovalStatus,
        approval_note: str | None,
        restored_from_revision: int | None,
        created_at: datetime,
        import_source: RulePackImportSource | None = None,
    ) -> RulePackVersionRecord:
        return RulePackVersionRecord(
            pack_id=pack_id,
            revision=revision,
            request_id=request_id,
            schema_version=spec.schema_version,
            json_payload=canonical_formatting_spec_json(spec),
            spec_sha256=formatting_spec_sha256(spec),
            source_type=spec.source.type.value,
            approval_status=approval_status.value,
            approval_note=approval_note,
            change_note=change_note,
            restored_from_revision=restored_from_revision,
            import_source_json=(
                json.dumps(
                    import_source.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if import_source
                else None
            ),
            import_source_artifact_sha256=(
                import_source.artifact_sha256 if import_source else None
            ),
            created_at=created_at,
        )

    def _existing_rule_pack_write(
        self,
        *,
        request_id: str,
        pack_id: str | None,
        spec: FormattingSpec,
        change_note: str,
        approval_status: RulePackApprovalStatus,
        approval_note: str | None,
        restored_from_revision: int | None,
        name: str | None = None,
        description: str | None = None,
        scope_label: str | None = None,
        import_source: RulePackImportSource | None = None,
    ) -> RulePackArtifact | None:
        with self.database.session_factory() as session:
            version = session.scalar(
                select(RulePackVersionRecord).where(RulePackVersionRecord.request_id == request_id)
            )
            if version is None:
                return None
            existing_pack_id = version.pack_id
            existing_revision = version.revision
        artifact = self.get_rule_pack_artifact(existing_pack_id, existing_revision)
        same_request = (
            (pack_id is None or artifact.pack_id == pack_id)
            and artifact.spec_sha256 == formatting_spec_sha256(spec)
            and artifact.change_note == change_note
            and artifact.approval_status == approval_status
            and artifact.approval_note == approval_note
            and artifact.restored_from_revision == restored_from_revision
            and artifact.import_source == import_source
            and (name is None or artifact.name == name)
            and (description is None or artifact.description == description)
            and (scope_label is None or artifact.scope_label == scope_label)
        )
        if not same_request:
            raise ApiError(
                409,
                "IDEMPOTENCY_KEY_REUSED",
                "The request identifier was already used for different rule-pack content.",
                {"request_id": request_id},
            )
        return artifact

    @staticmethod
    def _rule_pack_version_summary(
        version: RulePackVersionRecord,
    ) -> RulePackVersionSummary:
        return RulePackVersionSummary(
            revision=version.revision,
            approval_status=RulePackApprovalStatus(version.approval_status),
            approval_note=version.approval_note,
            change_note=version.change_note,
            restored_from_revision=version.restored_from_revision,
            spec_sha256=version.spec_sha256,
            source_type=version.source_type,
            created_at=version.created_at,
            import_source=ApiService._stored_rule_pack_import_source(version),
        )

    @staticmethod
    def _stored_rule_pack_import_source(
        version: RulePackVersionRecord,
    ) -> RulePackImportSource | None:
        if version.import_source_json is None and version.import_source_artifact_sha256 is None:
            return None
        if version.import_source_json is None or version.import_source_artifact_sha256 is None:
            raise ApiError(
                500,
                "RULE_PACK_INTEGRITY_FAILED",
                "The stored rule-pack import provenance is incomplete.",
                {"pack_id": version.pack_id, "revision": version.revision},
            )
        try:
            source = RulePackImportSource.model_validate_json(version.import_source_json)
        except ValidationError as exc:
            raise ApiError(
                500,
                "RULE_PACK_INTEGRITY_FAILED",
                "The stored rule-pack import provenance is invalid.",
                {"pack_id": version.pack_id, "revision": version.revision},
            ) from exc
        if version.import_source_artifact_sha256 != source.artifact_sha256:
            raise ApiError(
                500,
                "RULE_PACK_INTEGRITY_FAILED",
                "The stored rule-pack import provenance failed its integrity check.",
                {"pack_id": version.pack_id, "revision": version.revision},
            )
        return source

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

    def create_job(
        self,
        document_id: str,
        analysis_id: str,
        spec_id: str,
        *,
        processing_boundary_acknowledged: bool = False,
        acknowledgment_method: ProcessingBoundaryAcknowledgmentMethod = (
            ProcessingBoundaryAcknowledgmentMethod.EXPLICIT_SINGLE_JOB
        ),
        processing_boundary_acknowledged_at: datetime | None = None,
    ) -> JobRecord:
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
        boundary = self.get_analysis(analysis_id).summary.processing_boundary
        if boundary.acknowledgment_required and not processing_boundary_acknowledged:
            raise ApiError(
                409,
                "PROCESSING_BOUNDARY_ACKNOWLEDGMENT_REQUIRED",
                "Confirm the source document processing boundary before formatting.",
                {
                    "review_feature_count": boundary.review_feature_count,
                    "review_feature_codes": [
                        item.code for item in boundary.items if item.review_required
                    ],
                },
            )
        if boundary.acknowledgment_required:
            if acknowledgment_method not in {
                ProcessingBoundaryAcknowledgmentMethod.EXPLICIT_SINGLE_JOB,
                ProcessingBoundaryAcknowledgmentMethod.EXPLICIT_BATCH,
            }:
                raise ApiError(
                    500,
                    "PROCESSING_BOUNDARY_ACKNOWLEDGMENT_INVALID",
                    "The processing-boundary acknowledgment method is invalid.",
                )
            stored_acknowledgment = acknowledgment_method.value
            stored_acknowledged_at = as_utc(processing_boundary_acknowledged_at) or utcnow()
        else:
            stored_acknowledgment = ProcessingBoundaryAcknowledgmentMethod.NOT_REQUIRED.value
            stored_acknowledged_at = None
        record = JobRecord(
            id=f"job_{uuid.uuid4().hex}",
            document_id=document_id,
            analysis_id=analysis_id,
            spec_id=spec_id,
            status=JobStatus.QUEUED.value,
            progress=0,
            processing_boundary_acknowledgment=stored_acknowledgment,
            processing_boundary_acknowledged_at=stored_acknowledged_at,
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
                result_summary = build_job_result_summary(audit)
            except (OSError, UnicodeError, ValidationError):
                auto_layout_splits = 0
        return JobResponse(
            job_id=record.id,
            document_id=record.document_id,
            analysis_id=record.analysis_id,
            spec_id=record.spec_id,
            processing_boundary_acknowledgment=(
                ProcessingBoundaryAcknowledgmentMethod(record.processing_boundary_acknowledgment)
                if record.processing_boundary_acknowledgment
                else None
            ),
            processing_boundary_acknowledged_at=(
                as_utc(record.processing_boundary_acknowledged_at)
            ),
            status=JobStatus(record.status),
            progress=record.progress,
            auto_layout_splits=auto_layout_splits,
            result_summary=result_summary,
            output_document_url=f"/api/v1/jobs/{record.id}/output" if completed else None,
            delivery_package_url=(
                f"/api/v1/jobs/{record.id}/delivery-package.zip" if completed else None
            ),
            audit_json_url=(
                f"/api/v1/jobs/{record.id}/audit.json" if record.audit_json_path else None
            ),
            audit_markdown_url=(
                f"/api/v1/jobs/{record.id}/audit.md" if record.audit_markdown_path else None
            ),
            error_code=record.error_code,
            error_message=record.error_message,
            created_at=as_utc(record.created_at),
            updated_at=as_utc(record.updated_at),
        )

    def run_job(self, job_id: str) -> None:
        try:
            if self._set_job_state(job_id, JobStatus.ANALYZING, 15) != JobStatus.ANALYZING:
                return
            job = self.get_job(job_id)
            document = self.get_document(job.document_id)
            self.capacity.ensure(
                processing_working_bytes(document.size_bytes),
                operation="document_processing",
            )
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
            if self._set_job_state(job_id, JobStatus.PLANNING, 30) != JobStatus.PLANNING:
                return
            if self._set_job_state(job_id, JobStatus.FORMATTING, 45) != JobStatus.FORMATTING:
                return
            output = self.storage.output_path(job_id)
            artifacts = self.storage.job_dir(job_id)
            result = process_document(
                Path(document.stored_path),
                analysis.document_ir,
                spec,
                output,
                job_id=job_id,
                artifact_dir=artifacts,
                processing_boundary_acknowledgment_method=(
                    ProcessingBoundaryAcknowledgmentMethod(job.processing_boundary_acknowledgment)
                    if job.processing_boundary_acknowledgment
                    else None
                ),
                processing_boundary_acknowledged_at=(
                    as_utc(job.processing_boundary_acknowledged_at)
                ),
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
        except OSError as exc:
            if is_capacity_error(exc):
                mapped = self.capacity.api_error(operation="document_processing")
                self._fail_job(job_id, mapped.code, mapped.message)
            else:
                logger.exception("Local I/O failure", extra={"job_id": job_id})
                self._fail_job(
                    job_id,
                    "FORMAT_APPLICATION_FAILED",
                    "A local file operation failed during processing.",
                )
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
        finally:
            self._discard_canceled_job_artifacts(job_id)

    def _set_job_state(
        self,
        job_id: str,
        status: JobStatus,
        progress: int,
        **paths: str,
    ) -> JobStatus:
        with self.database.session_factory.begin() as session:
            record = session.get(JobRecord, job_id)
            if record is None:
                raise ApiError(404, "JOB_NOT_FOUND", "Processing job not found.")
            current = JobStatus(record.status)
            if record.cancel_requested or current in {
                JobStatus.CANCELING,
                JobStatus.CANCELED,
            }:
                record.cancel_requested = True
                record.status = JobStatus.CANCELED.value
                record.progress = 100
                record.output_path = None
                record.audit_json_path = None
                record.audit_markdown_path = None
                record.error_code = None
                record.error_message = None
                record.updated_at = utcnow()
                return JobStatus.CANCELED
            if current in {JobStatus.COMPLETED, JobStatus.FAILED}:
                return current
            record.status = status.value
            record.progress = progress
            record.updated_at = utcnow()
            for name, value in paths.items():
                setattr(record, name, value)
            return status

    def _fail_job(self, job_id: str, code: str, message: str) -> None:
        with self.database.session_factory.begin() as session:
            record = session.get(JobRecord, job_id)
            if record is None:
                return
            if record.cancel_requested or record.status in {
                JobStatus.CANCELING.value,
                JobStatus.CANCELED.value,
            }:
                record.cancel_requested = True
                record.status = JobStatus.CANCELED.value
                record.progress = 100
                record.output_path = None
                record.audit_json_path = None
                record.audit_markdown_path = None
                record.error_code = None
                record.error_message = None
                record.updated_at = utcnow()
                return
            audit_json = self.storage.job_dir(job_id) / "audit.json"
            audit_markdown = self.storage.job_dir(job_id) / "audit.md"
            record.status = JobStatus.FAILED.value
            record.error_code = code
            record.error_message = message
            record.audit_json_path = str(audit_json) if audit_json.exists() else None
            record.audit_markdown_path = str(audit_markdown) if audit_markdown.exists() else None
            record.updated_at = utcnow()

    def _discard_canceled_job_artifacts(self, job_id: str) -> None:
        with self.database.session_factory() as session:
            record = session.get(JobRecord, job_id)
            canceled = record is not None and record.status == JobStatus.CANCELED.value
        if canceled:
            self.storage.delete_job_artifacts(job_id)


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


def _display_filename(filename: str) -> str:
    cleaned = filename.replace("\\", "/").split("/")[-1].strip()
    return cleaned[:512] or "document.docx"


def _rule_pack_name_key(name: str) -> str:
    normalized = unicodedata.normalize("NFKC", name)
    return " ".join(normalized.casefold().split())
