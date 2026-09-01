from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from docalign_core.domain.base import StrictModel
from docalign_core.domain.workspace import StoragePressure


class DiagnosticOverall(StrEnum):
    READY = "ready"
    ATTENTION = "attention"
    ACTION_REQUIRED = "action_required"


class DiagnosticCheckStatus(StrEnum):
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


class DiagnosticCheck(StrictModel):
    check_id: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
    status: DiagnosticCheckStatus
    title: str
    detail: str
    remediation: str | None = None


class DiagnosticRuntime(StrictModel):
    application_version: str
    python_version: str
    operating_system: str
    operating_system_release: str
    architecture: str


class DiagnosticConfiguration(StrictModel):
    local_only: Literal[True] = True
    database_backend: str
    llm_configured: bool
    job_concurrency: int = Field(ge=1)
    max_upload_mb: int = Field(ge=1)
    max_batch_files: int = Field(ge=1)
    max_batch_total_mb: int = Field(ge=1)
    min_free_mb: int = Field(ge=1)


class DiagnosticDataSummary(StrictModel):
    docalign_bytes: int = Field(ge=0)
    disk_total_bytes: int = Field(ge=0)
    disk_free_bytes: int = Field(ge=0)
    storage_pressure: StoragePressure
    documents: int = Field(ge=0)
    analyses: int = Field(ge=0)
    jobs: int = Field(ge=0)
    active_jobs: int = Field(ge=0)
    failed_jobs: int = Field(ge=0)
    batches: int = Field(ge=0)
    rule_packs: int = Field(ge=0)


class DiagnosticErrorCodeCount(StrictModel):
    code: str = Field(pattern=r"^[A-Z0-9_]{1,64}$")
    count: int = Field(ge=1)


class SupportDiagnosticReport(StrictModel):
    schema_version: Literal["support-diagnostic.v1"] = "support-diagnostic.v1"
    generated_at: datetime
    overall: DiagnosticOverall
    runtime: DiagnosticRuntime
    configuration: DiagnosticConfiguration
    data_summary: DiagnosticDataSummary
    checks: list[DiagnosticCheck]
    recent_error_codes: list[DiagnosticErrorCodeCount]
    excluded_data: list[
        Literal[
            "document_content",
            "filenames",
            "record_identifiers",
            "local_paths",
            "database_connection_string",
            "model_endpoint",
            "credentials",
            "raw_logs",
        ]
    ]
