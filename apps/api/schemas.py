from __future__ import annotations

from datetime import datetime
from typing import Any

from docalign_core.domain.document_ir import RoleOverride
from docalign_core.domain.enums import AnalysisMode, JobStatus
from docalign_core.domain.formatting_spec import CleanupPresetCatalogItem, FormattingSpec
from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoleOverrideRequest(ApiModel):
    overrides: list[RoleOverride] = Field(default_factory=list)


class AnalyzeRequest(ApiModel):
    mode: AnalysisMode = AnalysisMode.DETERMINISTIC


class TextDocumentRequest(ApiModel):
    text: str = Field(min_length=1, max_length=1_000_000)
    filename: str = Field(default="未命名文档.docx", min_length=1, max_length=512)


class StructuredSpecRequest(ApiModel):
    document_id: str | None = None
    spec: FormattingSpec


class CompileSpecRequest(ApiModel):
    document_id: str | None = None
    analysis_id: str | None = None
    instruction: str = Field(min_length=1, max_length=20_000)
    apply_preset: bool = False


class ValidateSpecRequest(ApiModel):
    spec: dict[str, Any]


class JobCreateRequest(ApiModel):
    document_id: str
    analysis_id: str
    spec_id: str


class JobResultSummary(ApiModel):
    validation_passed: bool
    content_integrity_passed: bool
    format_operations: int = Field(ge=0)
    changed_mutations: int = Field(ge=0)
    change_categories: dict[str, int]
    warning_count: int = Field(ge=0)
    validation_issue_count: int = Field(ge=0)
    remaining_review_items: int = Field(ge=0)
    paragraphs_before: int | None = Field(ge=0)
    paragraphs_after: int | None = Field(ge=0)
    auto_layout_splits: int = Field(ge=0)


class JobResponse(ApiModel):
    job_id: str
    document_id: str
    analysis_id: str
    spec_id: str
    status: JobStatus
    progress: int = Field(ge=0, le=100)
    auto_layout_splits: int = Field(ge=0)
    result_summary: JobResultSummary | None
    output_document_url: str | None
    audit_json_url: str | None
    audit_markdown_url: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class CleanupPresetCatalogResponse(ApiModel):
    presets: list[CleanupPresetCatalogItem]


class ComplianceRequest(ApiModel):
    analysis_id: str
    spec_id: str
