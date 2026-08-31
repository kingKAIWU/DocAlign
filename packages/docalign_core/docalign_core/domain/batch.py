from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from docalign_core.domain.base import StrictModel


class BatchStatus(StrEnum):
    PREPARING = "preparing"
    PROCESSING = "processing"
    CANCELING = "canceling"
    CANCELED = "canceled"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"


class BatchItemStatus(StrEnum):
    PREPARING = "preparing"
    QUEUED = "queued"
    ANALYZING = "analyzing"
    PLANNING = "planning"
    FORMATTING = "formatting"
    VALIDATING = "validating"
    REPAIRING = "repairing"
    CANCELING = "canceling"
    CANCELED = "canceled"
    COMPLETED = "completed"
    FAILED = "failed"


class BatchAuditSummary(StrictModel):
    total: int = Field(ge=1)
    completed: int = Field(ge=0)
    failed: int = Field(ge=0)
    canceled: int = Field(ge=0)
    active: int = Field(ge=0)


class BatchAuditItem(StrictModel):
    item_id: str
    position: int = Field(ge=1)
    filename: str
    status: BatchItemStatus
    progress: int = Field(ge=0, le=100)
    source_sha256: str | None = None
    document_id: str | None = None
    analysis_id: str | None = None
    job_id: str | None = None
    attempt_count: int = Field(ge=0)
    retryable: bool
    error_code: str | None = None
    error_message: str | None = None
    validation_passed: bool | None = None
    content_integrity_passed: bool | None = None
    changed_mutations: int | None = Field(default=None, ge=0)
    source_review_features: int | None = Field(default=None, ge=0)
    output_document_url: str | None = None
    audit_json_url: str | None = None


class BatchAudit(StrictModel):
    schema_version: Literal["batch-audit.v2"] = "batch-audit.v2"
    batch_id: str
    request_id: str
    name: str
    status: BatchStatus
    progress: int = Field(ge=0, le=100)
    rule_pack_id: str
    rule_pack_revision: int = Field(ge=1)
    rule_pack_name: str
    rule_pack_spec_sha256: str
    processing_boundary_acknowledged: bool = False
    summary: BatchAuditSummary
    items: list[BatchAuditItem]
    output_zip_url: str | None = None
    delivery_package_url: str | None = None
    audit_json_url: str
    created_at: datetime
    updated_at: datetime
