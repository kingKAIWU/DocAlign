from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from docalign_core.config import Settings
from docalign_core.delivery import DeliveryPackageError
from docalign_core.docx.safety import DocxSafetyError
from docalign_core.domain.batch import BatchAudit
from docalign_core.domain.compliance import ComplianceReport
from docalign_core.domain.delivery import DeliveryPackageVerification
from docalign_core.domain.diagnostics import SupportDiagnosticReport
from docalign_core.domain.formatting_spec import (
    FormattingSpec,
    cleanup_preset_catalog,
    default_academic_spec,
    default_cleanup_spec,
)
from docalign_core.domain.manifest import FormatManifest
from docalign_core.domain.rule_pack import RulePackArtifact
from docalign_core.domain.template_candidate import TemplateRuleCandidate
from docalign_core.domain.workspace import WorkspaceStorageReport
from fastapi import BackgroundTasks, FastAPI, File, Form, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from apps.api.batches import BatchService
from apps.api.capacity import MEBIBYTE, WorkspaceCapacityGuard, is_capacity_error
from apps.api.db import Database
from apps.api.deliveries import DeliveryService
from apps.api.diagnostics import DiagnosticService
from apps.api.errors import ApiError
from apps.api.migrations import upgrade_database
from apps.api.runner import JobRunner
from apps.api.schemas import (
    AnalyzeRequest,
    BatchRetryRequest,
    CleanupPresetCatalogResponse,
    CompileSpecRequest,
    ComplianceRequest,
    JobCreateRequest,
    JobResponse,
    RoleOverrideRequest,
    RulePackCatalogResponse,
    RulePackCreateRequest,
    RulePackDetailResponse,
    RulePackImportPreview,
    RulePackImportResult,
    RulePackRestoreRequest,
    RulePackVersionCreateRequest,
    StructuredSpecRequest,
    TextDocumentRequest,
    ValidateSpecRequest,
)
from apps.api.service import ApiService
from apps.api.storage import LocalStorage
from apps.api.workspace import WorkspaceService
from apps.api.workspace_backups import WorkspaceBackupService


def create_app(
    settings: Settings | None = None,
    static_dir: Path | None = None,
    desktop_shutdown: Callable[[], None] | None = None,
) -> FastAPI:
    settings = settings or Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    database = Database(settings.database_url)
    storage = LocalStorage(settings.data_dir)
    capacity = WorkspaceCapacityGuard(storage.root, reserve_bytes=settings.min_free_mb * MEBIBYTE)
    service = ApiService(settings, database, storage, capacity)
    batch_service = BatchService(service, settings, database, storage, capacity)
    delivery_service = DeliveryService(service, batch_service, settings, storage, capacity)
    workspace_service = WorkspaceService(database, storage, batch_service, capacity)
    workspace_backup_service = WorkspaceBackupService(database, storage, capacity)
    diagnostic_service = DiagnosticService(settings, database, storage)
    runner = JobRunner(service, settings.job_concurrency)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        upgrade_database(database)
        database.create_all()
        database.mark_interrupted_jobs()
        await runner.start()
        yield
        await runner.close()

    application = FastAPI(
        title="DocAlign API",
        version="0.1.0",
        description="Local deterministic DOCX formatting and compliance API.",
        lifespan=lifespan,
    )
    application.state.settings = settings
    application.state.database = database
    application.state.storage = storage
    application.state.capacity = capacity
    application.state.service = service
    application.state.batch_service = batch_service
    application.state.delivery_service = delivery_service
    application.state.workspace_service = workspace_service
    application.state.workspace_backup_service = workspace_backup_service
    application.state.diagnostic_service = diagnostic_service
    application.state.runner = runner
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @application.exception_handler(ApiError)
    async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error_response(exc.status_code, exc.code, exc.message, exc.details)

    @application.exception_handler(OSError)
    async def handle_os_error(_: Request, exc: OSError) -> JSONResponse:
        if is_capacity_error(exc):
            mapped = capacity.api_error(operation="local_write")
            return _error_response(mapped.status_code, mapped.code, mapped.message, mapped.details)
        return _error_response(
            500,
            "LOCAL_IO_FAILED",
            "DocAlign could not complete a local file operation.",
        )

    @application.exception_handler(SQLAlchemyError)
    async def handle_database_error(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        if is_capacity_error(exc):
            mapped = capacity.api_error(operation="database_write")
            return _error_response(mapped.status_code, mapped.code, mapped.message, mapped.details)
        return _error_response(
            500,
            "DATABASE_OPERATION_FAILED",
            "DocAlign could not complete a local database operation.",
        )

    @application.exception_handler(DocxSafetyError)
    async def handle_docx_error(_: Request, exc: DocxSafetyError) -> JSONResponse:
        status = 413 if exc.code in {"FILE_TOO_LARGE", "DOCX_ZIP_BOMB"} else 422
        return _error_response(status, exc.code, exc.message, exc.details)

    @application.exception_handler(DeliveryPackageError)
    async def handle_delivery_error(_: Request, exc: DeliveryPackageError) -> JSONResponse:
        status = 413 if exc.code == "DELIVERY_PACKAGE_TOO_LARGE" else 422
        return _error_response(status, exc.code, exc.message, exc.details)

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response(
            422,
            "REQUEST_VALIDATION_FAILED",
            "The request payload is invalid.",
            {"errors": jsonable_encoder(exc.errors())},
        )

    @application.get("/api/v1/health")
    def health() -> dict[str, object]:
        return {"status": "ok", "version": "0.1.0"}

    @application.get("/api/v1/capabilities")
    def capabilities() -> dict[str, object]:
        return {
            "docx": True,
            "structured_spec": True,
            "llm_configured": settings.llm_configured,
            "llm_protocol": "openai-compatible-chat-completions",
            "smart_semantic_analysis": settings.llm_configured,
            "smart_analysis_sends_paragraph_text": True,
            "auto_layout": True,
            "default_cleanup_preset": True,
            "audit_only": True,
            "format_manifest": True,
            "template_rule_candidate": True,
            "rule_pack_library": True,
            "rule_pack_import": True,
            "batch_processing": True,
            "verifiable_delivery_packages": True,
            "verifiable_workspace_backup": True,
            "safe_workspace_restore": True,
            "max_batch_files": settings.max_batch_files,
            "max_batch_total_mb": settings.max_batch_total_mb,
            "min_free_mb": settings.min_free_mb,
            "max_delivery_package_mb": settings.max_batch_total_mb + 20,
            "max_upload_mb": settings.max_upload_mb,
            "max_rule_pack_import_kb": settings.max_rule_pack_import_kb,
            "local_only": True,
            "desktop_app": desktop_shutdown is not None,
        }

    @application.post("/api/v1/system/quit", status_code=202)
    def quit_desktop(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
        if desktop_shutdown is None:
            raise ApiError(
                404,
                "DESKTOP_ACTION_UNAVAILABLE",
                "The desktop shutdown action is not available in this runtime.",
            )
        if request.headers.get("x-docalign-action") != "quit":
            raise ApiError(
                403,
                "DESKTOP_ACTION_FORBIDDEN",
                "The desktop shutdown action requires an explicit same-origin request.",
            )
        fetch_site = request.headers.get("sec-fetch-site")
        if fetch_site and fetch_site not in {"same-origin", "none"}:
            raise ApiError(
                403,
                "DESKTOP_ACTION_FORBIDDEN",
                "Cross-site desktop actions are not allowed.",
            )
        origin = request.headers.get("origin")
        expected_origin = f"{request.url.scheme}://{request.url.netloc}"
        if origin and origin != expected_origin:
            raise ApiError(
                403,
                "DESKTOP_ACTION_FORBIDDEN",
                "Cross-origin desktop actions are not allowed.",
            )
        background_tasks.add_task(desktop_shutdown)
        return {"status": "shutting_down"}

    @application.get("/api/v1/diagnostics")
    def diagnostics() -> SupportDiagnosticReport:
        return diagnostic_service.report()

    @application.get("/api/v1/diagnostics/export")
    def export_diagnostics(response: Response) -> SupportDiagnosticReport:
        response.headers["Content-Disposition"] = (
            'attachment; filename="docalign-support-diagnostic.json"'
        )
        return diagnostic_service.report()

    @application.post("/api/v1/deliveries/verify")
    async def verify_delivery(file: UploadFile) -> DeliveryPackageVerification:
        return await delivery_service.verify_upload(file)

    @application.get("/api/v1/workspace/storage")
    def workspace_storage(
        item_limit: int = Query(default=50, ge=1, le=200),
    ) -> WorkspaceStorageReport:
        return workspace_service.storage_report(item_limit=item_limit)

    @application.get(
        "/api/v1/workspace/backup",
        response_class=FileResponse,
        responses={200: {"content": {"application/zip": {}}}},
    )
    def download_workspace_backup(background_tasks: BackgroundTasks) -> FileResponse:
        artifact = workspace_backup_service.create()
        background_tasks.add_task(artifact.cleanup)
        return FileResponse(
            artifact.path,
            media_type="application/zip",
            filename=artifact.filename,
            background=background_tasks,
        )

    @application.get("/api/v1/presets/generic-academic-cn")
    def generic_academic_preset() -> dict[str, object]:
        return {
            "preset_id": "generic-academic-cn",
            "spec": default_academic_spec().model_dump(mode="json"),
        }

    @application.get("/api/v1/presets/default-clean-cn")
    def default_cleanup_preset() -> dict[str, object]:
        return {
            "preset_id": "default-clean-cn",
            "spec": default_cleanup_spec().model_dump(mode="json"),
        }

    @application.get("/api/v1/presets")
    def cleanup_presets() -> CleanupPresetCatalogResponse:
        return CleanupPresetCatalogResponse(presets=cleanup_preset_catalog())

    @application.post("/api/v1/documents", status_code=201)
    async def upload_document(file: UploadFile) -> dict[str, object]:
        return await service.create_document(file)

    @application.post("/api/v1/templates/candidate")
    async def create_template_candidate(file: UploadFile) -> TemplateRuleCandidate:
        return await service.compile_template_candidate(file)

    @application.post("/api/v1/documents/from-text", status_code=201)
    def create_document_from_text(request: TextDocumentRequest) -> dict[str, object]:
        return service.create_text_document(request.text, request.filename)

    @application.get("/api/v1/documents/{document_id}")
    def get_document(document_id: str) -> dict[str, object]:
        return service.document_payload(service.get_document(document_id))

    @application.get("/api/v1/documents/{document_id}/source")
    def get_document_source(document_id: str) -> FileResponse:
        record = service.get_document(document_id)
        return FileResponse(
            record.stored_path,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=record.original_filename,
        )

    @application.get("/api/v1/documents/{document_id}/format-manifest")
    def get_format_manifest(document_id: str, response: Response) -> FormatManifest:
        response.headers["Content-Disposition"] = 'attachment; filename="format-manifest.json"'
        return service.extract_manifest(document_id)

    @application.delete("/api/v1/documents/{document_id}", status_code=204)
    def delete_document(document_id: str) -> Response:
        service.delete_document(document_id)
        return Response(status_code=204)

    @application.post("/api/v1/documents/{document_id}/analyze", status_code=201)
    async def analyze_document(
        document_id: str, request: AnalyzeRequest | None = None
    ) -> dict[str, object]:
        analysis_id, result = await service.analyze(
            document_id, request.mode if request else AnalyzeRequest().mode
        )
        return {"analysis_id": analysis_id, **result.model_dump(mode="json")}

    @application.get("/api/v1/analyses/{analysis_id}")
    def get_analysis(analysis_id: str) -> dict[str, object]:
        return {
            "analysis_id": analysis_id,
            **service.get_analysis(analysis_id).model_dump(mode="json"),
        }

    @application.put("/api/v1/analyses/{analysis_id}/role-overrides")
    def set_role_overrides(analysis_id: str, request: RoleOverrideRequest) -> dict[str, object]:
        return {
            "analysis_id": analysis_id,
            **service.set_role_overrides(analysis_id, request.overrides).model_dump(mode="json"),
        }

    @application.post("/api/v1/specs", status_code=201)
    def create_spec(request: StructuredSpecRequest) -> dict[str, object]:
        spec_id, spec = service.create_spec(request.spec, request.document_id)
        return {"spec_id": spec_id, "spec": spec.model_dump(mode="json")}

    @application.post("/api/v1/specs/compile", status_code=201)
    async def compile_spec(request: CompileSpecRequest) -> dict[str, object]:
        spec_id, compilation = await service.compile_spec(
            request.instruction,
            document_id=request.document_id,
            analysis_id=request.analysis_id,
            apply_preset=request.apply_preset,
        )
        return {"spec_id": spec_id, **compilation.model_dump(mode="json")}

    @application.post("/api/v1/specs/validate")
    def validate_spec(request: ValidateSpecRequest) -> dict[str, object]:
        spec = FormattingSpec.model_validate(request.spec)
        return {"valid": True, "spec": spec.model_dump(mode="json")}

    @application.get("/api/v1/specs/{spec_id}")
    def get_spec(spec_id: str) -> dict[str, object]:
        return {"spec_id": spec_id, "spec": service.get_spec(spec_id).model_dump(mode="json")}

    @application.put("/api/v1/specs/{spec_id}")
    def update_spec(spec_id: str, request: StructuredSpecRequest) -> dict[str, object]:
        spec = service.update_spec(spec_id, request.spec)
        return {"spec_id": spec_id, "spec": spec.model_dump(mode="json")}

    @application.get("/api/v1/rule-packs")
    def list_rule_packs() -> RulePackCatalogResponse:
        return service.list_rule_packs()

    @application.post("/api/v1/rule-packs", status_code=201)
    def create_rule_pack(request: RulePackCreateRequest) -> RulePackArtifact:
        return service.create_rule_pack(
            request_id=request.request_id,
            name=request.name,
            description=request.description,
            scope_label=request.scope_label,
            spec=request.spec,
            change_note=request.change_note,
            approval_status=request.approval_status,
            approval_note=request.approval_note,
        )

    @application.post("/api/v1/rule-packs/imports/preview")
    async def preview_rule_pack_import(
        file: Annotated[UploadFile, File()],
    ) -> RulePackImportPreview:
        return await service.preview_rule_pack_import(file)

    @application.post("/api/v1/rule-packs/imports", status_code=201)
    async def import_rule_pack(
        request_id: Annotated[
            str,
            Form(pattern=r"^[A-Za-z0-9_-]{8,64}$"),
        ],
        name: Annotated[str, Form(min_length=1, max_length=120)],
        file: Annotated[UploadFile, File()],
    ) -> RulePackImportResult:
        return await service.import_rule_pack(
            file,
            request_id=request_id,
            name=name,
        )

    @application.get("/api/v1/rule-packs/{pack_id}")
    def get_rule_pack(pack_id: str) -> RulePackDetailResponse:
        return service.get_rule_pack_detail(pack_id)

    @application.post("/api/v1/rule-packs/{pack_id}/versions", status_code=201)
    def create_rule_pack_version(
        pack_id: str, request: RulePackVersionCreateRequest
    ) -> RulePackArtifact:
        return service.create_rule_pack_version(
            pack_id,
            request_id=request.request_id,
            spec=request.spec,
            change_note=request.change_note,
            approval_status=request.approval_status,
            approval_note=request.approval_note,
        )

    @application.get("/api/v1/rule-packs/{pack_id}/versions/{revision}")
    def get_rule_pack_version(pack_id: str, revision: int) -> RulePackArtifact:
        return service.get_rule_pack_artifact(pack_id, revision)

    @application.post("/api/v1/rule-packs/{pack_id}/restore", status_code=201)
    def restore_rule_pack_version(
        pack_id: str, request: RulePackRestoreRequest
    ) -> RulePackArtifact:
        return service.restore_rule_pack_revision(
            pack_id, request.revision, request.change_note, request.request_id
        )

    @application.get("/api/v1/rule-packs/{pack_id}/versions/{revision}/export")
    def export_rule_pack_version(
        pack_id: str, revision: int, response: Response
    ) -> RulePackArtifact:
        response.headers["Content-Disposition"] = (
            f'attachment; filename="{pack_id}-r{revision}.rule-pack.json"'
        )
        return service.get_rule_pack_artifact(pack_id, revision)

    @application.post("/api/v1/jobs", status_code=202)
    async def create_job(request: JobCreateRequest) -> JobResponse:
        record = service.create_job(
            request.document_id,
            request.analysis_id,
            request.spec_id,
            processing_boundary_acknowledged=request.processing_boundary_acknowledged,
        )
        await runner.enqueue(record.id)
        return service.job_payload(record)

    @application.post("/api/v1/batches", status_code=202)
    async def create_batch(
        request_id: Annotated[str, Form()],
        name: Annotated[str, Form()],
        rule_pack_id: Annotated[str, Form()],
        rule_pack_revision: Annotated[int, Form(ge=1)],
        processing_boundary_acknowledged: Annotated[bool, Form()],
        files: Annotated[list[UploadFile], File()],
    ) -> BatchAudit:
        audit, job_ids = await batch_service.create_batch(
            request_id=request_id,
            name=name,
            rule_pack_id=rule_pack_id,
            rule_pack_revision=rule_pack_revision,
            processing_boundary_acknowledged=processing_boundary_acknowledged,
            files=files,
        )
        for job_id in job_ids:
            await runner.enqueue(job_id)
        return audit

    @application.get("/api/v1/batches/{batch_id}")
    def get_batch(batch_id: str) -> BatchAudit:
        return batch_service.get_batch(batch_id)

    @application.post("/api/v1/batches/{batch_id}/cancel", status_code=202)
    def cancel_batch(batch_id: str) -> BatchAudit:
        return batch_service.cancel_batch(batch_id)

    @application.delete("/api/v1/batches/{batch_id}", status_code=204)
    def delete_batch(batch_id: str) -> Response:
        batch_service.delete_batch(batch_id)
        return Response(status_code=204)

    @application.post("/api/v1/batches/{batch_id}/items/{item_id}/retry", status_code=202)
    async def retry_batch_item(
        batch_id: str, item_id: str, request: BatchRetryRequest
    ) -> BatchAudit:
        audit, job_ids = await batch_service.retry_item(batch_id, item_id, request.request_id)
        for job_id in job_ids:
            await runner.enqueue(job_id)
        return audit

    @application.get("/api/v1/batches/{batch_id}/audit.json")
    def get_batch_audit(batch_id: str, response: Response) -> BatchAudit:
        response.headers["Content-Disposition"] = f'attachment; filename="{batch_id}-audit.json"'
        return batch_service.get_batch(batch_id)

    @application.get("/api/v1/batches/{batch_id}/outputs.zip")
    def get_batch_outputs(batch_id: str) -> FileResponse:
        return FileResponse(
            batch_service.build_output_zip(batch_id),
            media_type="application/zip",
            filename=f"{batch_id}-outputs.zip",
        )

    @application.get("/api/v1/batches/{batch_id}/delivery-package.zip")
    def get_batch_delivery_package(batch_id: str) -> FileResponse:
        return FileResponse(
            delivery_service.build_batch_package(batch_id),
            media_type="application/zip",
            filename=f"{batch_id}-delivery-package.zip",
        )

    @application.post("/api/v1/documents/{document_id}/compliance")
    def audit_document_compliance(document_id: str, request: ComplianceRequest) -> ComplianceReport:
        return service.audit_compliance(
            document_id,
            request.analysis_id,
            request.spec_id,
        )

    @application.get("/api/v1/jobs/{job_id}")
    def get_job(job_id: str) -> JobResponse:
        return service.job_payload(service.get_job(job_id))

    @application.get("/api/v1/jobs/{job_id}/output")
    def get_job_output(job_id: str) -> FileResponse:
        job = service.get_job(job_id)
        if job.status != "completed" or not job.output_path:
            raise ApiError(409, "JOB_NOT_COMPLETED", "The formatted document is not available.")
        document = service.get_document(job.document_id)
        filename = f"{Path(document.original_filename).stem}_formatted.docx"
        return FileResponse(
            job.output_path,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=filename,
        )

    @application.get("/api/v1/jobs/{job_id}/delivery-package.zip")
    def get_job_delivery_package(job_id: str) -> FileResponse:
        return FileResponse(
            delivery_service.build_job_package(job_id),
            media_type="application/zip",
            filename=f"{job_id}-delivery-package.zip",
        )

    @application.get("/api/v1/jobs/{job_id}/audit.json")
    def get_job_audit_json(job_id: str) -> FileResponse:
        job = service.get_job(job_id)
        if not job.audit_json_path or not Path(job.audit_json_path).exists():
            raise ApiError(404, "AUDIT_NOT_FOUND", "The JSON audit is not available.")
        return FileResponse(
            job.audit_json_path, media_type="application/json", filename="audit.json"
        )

    @application.get("/api/v1/jobs/{job_id}/audit.md")
    def get_job_audit_markdown(job_id: str) -> FileResponse:
        job = service.get_job(job_id)
        if not job.audit_markdown_path or not Path(job.audit_markdown_path).exists():
            raise ApiError(404, "AUDIT_NOT_FOUND", "The Markdown audit is not available.")
        return FileResponse(
            job.audit_markdown_path, media_type="text/markdown", filename="audit.md"
        )

    if static_dir is not None:
        web_root = static_dir.resolve()
        if not (web_root / "index.html").is_file():
            raise ValueError(f"Static web build is missing index.html: {web_root}")
        application.mount("/", StaticFiles(directory=web_root, html=True), name="web")

    return application


def _error_response(
    status_code: int,
    code: str,
    message: str,
    details: dict[str, object] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details or {}}},
    )
