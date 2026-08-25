from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from docalign_core.config import Settings
from docalign_core.docx.safety import DocxSafetyError
from docalign_core.domain.compliance import ComplianceReport
from docalign_core.domain.formatting_spec import (
    FormattingSpec,
    cleanup_preset_catalog,
    default_academic_spec,
    default_cleanup_spec,
)
from docalign_core.domain.manifest import FormatManifest
from fastapi import FastAPI, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

from apps.api.db import Database
from apps.api.errors import ApiError
from apps.api.runner import JobRunner
from apps.api.schemas import (
    AnalyzeRequest,
    CompileSpecRequest,
    ComplianceRequest,
    JobCreateRequest,
    RoleOverrideRequest,
    StructuredSpecRequest,
    TextDocumentRequest,
    ValidateSpecRequest,
)
from apps.api.service import ApiService
from apps.api.storage import LocalStorage


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    database = Database(settings.database_url)
    storage = LocalStorage(settings.data_dir)
    service = ApiService(settings, database, storage)
    runner = JobRunner(service, settings.job_concurrency)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
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
    application.state.service = service
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

    @application.exception_handler(DocxSafetyError)
    async def handle_docx_error(_: Request, exc: DocxSafetyError) -> JSONResponse:
        status = 413 if exc.code in {"FILE_TOO_LARGE", "DOCX_ZIP_BOMB"} else 422
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
            "max_upload_mb": settings.max_upload_mb,
            "local_only": True,
        }

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
    def cleanup_presets() -> dict[str, object]:
        presets: list[dict[str, object]] = []
        for item in cleanup_preset_catalog():
            spec = item["spec"]
            if not isinstance(spec, FormattingSpec):
                raise TypeError("Cleanup preset catalog contains an invalid spec.")
            presets.append(
                {
                    **{key: value for key, value in item.items() if key != "spec"},
                    "spec": spec.model_dump(mode="json"),
                }
            )
        return {"presets": presets}

    @application.post("/api/v1/documents", status_code=201)
    async def upload_document(file: UploadFile) -> dict[str, object]:
        return await service.create_document(file)

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

    @application.post("/api/v1/jobs", status_code=202)
    async def create_job(request: JobCreateRequest) -> dict[str, object]:
        record = service.create_job(request.document_id, request.analysis_id, request.spec_id)
        await runner.enqueue(record.id)
        return service.job_payload(record)

    @application.post("/api/v1/documents/{document_id}/compliance")
    def audit_document_compliance(document_id: str, request: ComplianceRequest) -> ComplianceReport:
        return service.audit_compliance(
            document_id,
            request.analysis_id,
            request.spec_id,
        )

    @application.get("/api/v1/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, object]:
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


app = create_app()
