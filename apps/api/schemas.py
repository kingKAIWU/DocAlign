from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from docalign_core.domain.document_ir import RoleOverride
from docalign_core.domain.enums import AnalysisMode, JobStatus
from docalign_core.domain.formatting_spec import CleanupPresetCatalogItem, FormattingSpec
from docalign_core.domain.rule_pack import (
    RulePackApprovalStatus,
    RulePackArtifact,
    RulePackImportSource,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class RulePackCreateRequest(ApiModel):
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    scope_label: str = Field(min_length=1, max_length=240)
    spec: FormattingSpec
    change_note: str = Field(default="创建初始修订", min_length=1, max_length=1_000)
    approval_status: RulePackApprovalStatus = RulePackApprovalStatus.DRAFT
    approval_note: str | None = Field(default=None, max_length=1_000)

    @field_validator("name", "scope_label", "change_note")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must contain visible characters")
        return stripped

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: str) -> str:
        return value.strip()

    @field_validator("approval_note")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def require_approval_note(self) -> RulePackCreateRequest:
        if (
            self.approval_status == RulePackApprovalStatus.LOCALLY_APPROVED
            and not self.approval_note
        ):
            raise ValueError("locally approved revisions require an approval note")
        return self


class RulePackVersionCreateRequest(ApiModel):
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    spec: FormattingSpec
    change_note: str = Field(min_length=1, max_length=1_000)
    approval_status: RulePackApprovalStatus = RulePackApprovalStatus.DRAFT
    approval_note: str | None = Field(default=None, max_length=1_000)

    @field_validator("change_note")
    @classmethod
    def strip_change_note(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("change_note must contain visible characters")
        return stripped

    @field_validator("approval_note")
    @classmethod
    def strip_approval_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def require_approval_note(self) -> RulePackVersionCreateRequest:
        if (
            self.approval_status == RulePackApprovalStatus.LOCALLY_APPROVED
            and not self.approval_note
        ):
            raise ValueError("locally approved revisions require an approval note")
        return self


class RulePackRestoreRequest(ApiModel):
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    revision: int = Field(ge=1)
    change_note: str = Field(default="恢复历史修订", min_length=1, max_length=1_000)

    @field_validator("change_note")
    @classmethod
    def strip_change_note(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("change_note must contain visible characters")
        return stripped


class RulePackCatalogItem(ApiModel):
    pack_id: str
    name: str
    description: str
    scope_label: str
    current_revision: int = Field(ge=1)
    current_approval_status: RulePackApprovalStatus
    current_spec_sha256: str
    created_at: datetime
    updated_at: datetime


class RulePackCatalogResponse(ApiModel):
    rule_packs: list[RulePackCatalogItem]


class RulePackVersionSummary(ApiModel):
    revision: int = Field(ge=1)
    approval_status: RulePackApprovalStatus
    approval_note: str | None
    change_note: str
    restored_from_revision: int | None = Field(default=None, ge=1)
    spec_sha256: str
    source_type: str
    created_at: datetime
    import_source: RulePackImportSource | None = None


class RulePackDetailResponse(ApiModel):
    pack_id: str
    name: str
    description: str
    scope_label: str
    current_revision: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
    versions: list[RulePackVersionSummary]


class RulePackImportPreview(ApiModel):
    integrity_verified: Literal[True] = True
    signature_status: Literal["unsigned"] = "unsigned"
    source: RulePackImportSource
    suggested_name: str = Field(min_length=1, max_length=120)
    source_name_conflict: bool
    already_present: bool
    existing_pack_id: str | None = None
    existing_revision: int | None = Field(default=None, ge=1)
    target_approval_status: Literal[RulePackApprovalStatus.DRAFT] = RulePackApprovalStatus.DRAFT
    warnings: list[str]


class RulePackImportResult(ApiModel):
    artifact: RulePackArtifact
    already_present: bool


class JobCreateRequest(ApiModel):
    document_id: str
    analysis_id: str
    spec_id: str


class BatchRetryRequest(ApiModel):
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")


class JobChangeDetail(ApiModel):
    locator: str | None
    node_id: str | None
    category: str
    property_path: str
    before_value: str | None
    after_value: str | None


class JobResultSummary(ApiModel):
    validation_passed: bool
    content_integrity_passed: bool
    format_operations: int = Field(ge=0)
    changed_mutations: int = Field(ge=0)
    change_categories: dict[str, int]
    change_details: list[JobChangeDetail]
    change_details_truncated: bool
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
